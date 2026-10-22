from __future__ import annotations

import base64
import json
import ssl
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

from remote_agent.modeljournal import ModelJournal, ModelUnresolved, usage_summary
from remote_agent.openaiprovider import OpenAIProvider
from remote_agent.providers import ProviderError
from remote_agent.service import ConfigurationError, load_config
from remote_agent.storage import canonical, digest
from remote_agent.store import Store
from tests.contracts.runtime.openaiprovider import OpenAIProviderFixture
from tests.contracts.runtime.required import require_value
from tests.paths import CONTRACTS_ROOT


@pytest.mark.contract_linux
class TestOpenAIProvider(OpenAIProviderFixture):
    def test_exact_prepared_https_request_and_bounded_complete_response_are_journaled(
        self,
    ) -> None:
        self.assertEqual(self.request["body"]["model"], "gpt-4.1-mini-2025-04-14")
        self.assertEqual(
            self.request["body"]["text"]["format"], {"type": "json_object"}
        )
        self.assertFalse(self.request["body"]["store"])
        self.assertEqual(
            json.loads(self.request["body"]["input"])["bundle"], self.bundle
        )
        with Store(self.root / "state.sqlite3") as store:

            def execute(request: Any) -> Any:
                self.assertEqual(store.records()[-1]["data"]["request"], request)
                execution, raw, connections = self.worker()
                connection = connections[0]
                self.assertEqual(
                    (connection.host, connection.sent[:2]),
                    ("api.openai.com", ("POST", "/v1/responses")),
                )
                self.assertEqual(
                    connection.options["context"].verify_mode, ssl.CERT_REQUIRED
                )
                self.assertTrue(connection.options["context"].check_hostname)
                self.assertEqual(connection.sent[2], canonical(request["body"]))
                self.assertEqual(
                    base64.b64decode(raw["body"]), json.dumps(self.api_body()).encode()
                )
                return execution

            execution = ModelJournal(store).run(
                "op1",
                "respond",
                self.provider.identity,
                lambda: self.request,
                execute,
                max_calls=2,
                timeout_ms=1000,
                usage=self.provider.usage,
            )
            self.assertEqual(self.provider.decode("respond", execution), self.reply)
            self.assertEqual(
                [r["kind"] for r in store.records()], ["model_input", "model_output"]
            )
            self.assertNotIn(
                "private-api-credential-sentinel", json.dumps(store.records())
            )

    def test_durable_usage_is_not_counted_twice_on_recovery_and_has_explicit_unknowns(
        self,
    ) -> None:
        with Store(self.root / "usage.sqlite3") as store:
            journal = ModelJournal(store)
            execution = self.worker()[0]
            for _ in range(2):
                journal.run(
                    "same",
                    "respond",
                    self.provider.identity,
                    lambda: self.request,
                    lambda _: execution,
                    max_calls=3,
                    timeout_ms=1000,
                    usage=self.provider.usage,
                )
            unknown = {
                "profile": "remote-agent/callback-result/1",
                "value": {"answer": "legacy"},
                "failure": None,
                "uncertain": False,
            }
            journal.run(
                "legacy",
                "respond",
                {"profile": "fixture"},
                dict,
                lambda _: unknown,
                max_calls=3,
                timeout_ms=1000,
            )
            self.assertEqual(
                usage_summary(store),
                {
                    "reported_calls": 1,
                    "unreported_calls": 1,
                    "input_tokens": 120,
                    "cached_input_tokens": 20,
                    "output_tokens": 10,
                    "costed_calls": 1,
                    "estimated_cost_micro_usd": 58,
                    "currency": "USD",
                },
            )

    def test_concurrent_model_results_do_not_lose_usage_updates(self) -> None:
        execution = self.worker()[0]
        with Store(self.root / "concurrent.sqlite3") as store:
            journal = ModelJournal(store)

            def call(index: Any) -> Any:
                return journal.run(
                    str(index),
                    "respond",
                    self.provider.identity,
                    lambda: self.request,
                    lambda _: execution,
                    max_calls=8,
                    timeout_ms=1000,
                    usage=self.provider.usage,
                )

            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(len(list(pool.map(call, range(8)))), 8)
            summary = usage_summary(store)
            self.assertEqual(
                (
                    summary["reported_calls"],
                    summary["unreported_calls"],
                    summary["input_tokens"],
                    summary["estimated_cost_micro_usd"],
                ),
                (8, 0, 960, 464),
            )

    def test_uncertain_server_error_is_held_with_its_original_result(self) -> None:
        execution = self.worker(
            status=503, body={"error": {"message": "private-api-credential-sentinel"}}
        )[0]
        with Store(self.root / "failure.sqlite3") as store:
            journal = ModelJournal(store)
            for i in range(2):
                result = journal.run(
                    "uncertain",
                    "respond",
                    self.provider.identity,
                    lambda: self.request,
                    lambda _, *, i=i: execution if i == 0 else self.fail("resampled"),
                    max_calls=2,
                    timeout_ms=1000,
                    usage=self.provider.usage,
                )
                with self.assertRaises(ModelUnresolved):
                    self.provider.decode("respond", result)
            self.assertEqual(store.get("model_calls"), 1)

    def test_capacity_and_incomplete_or_refused_outputs_never_become_a_reply(
        self,
    ) -> None:
        too_large = self.worker(raw=b"x" * 131073)[0]
        with self.assertRaises(ModelUnresolved):
            self.provider.decode("respond", too_large)
        for body in (
            self.api_body(status="incomplete"),
            self.api_body(
                output=[
                    {
                        "type": "message",
                        "content": [{"type": "refusal", "refusal": "Cannot comply"}],
                    }
                ]
            ),
            self.api_body(
                output=[
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"state":{},"state":{}}'}
                        ],
                    }
                ]
            ),
        ):
            with self.assertRaises(ProviderError):
                self.provider.decode("respond", self.worker(body=body)[0])

    def test_http_rate_limit_is_definitive_and_credentials_are_redacted_from_error_body(
        self,
    ) -> None:
        execution, result, _ = self.worker(
            status=429, body={"error": {"message": "private-api-credential-sentinel"}}
        )
        self.assertFalse(result["uncertain"])
        self.assertTrue(result["redacted"])
        with self.assertRaises(ProviderError):
            self.provider.decode("respond", execution)

    def test_credential_echo_in_request_id_also_reports_redaction(self) -> None:
        _, result, _ = self.worker(request_id="private-api-credential-sentinel")
        self.assertEqual(result["request_id"], "[REDACTED_CREDENTIAL]")
        self.assertTrue(result["redacted"])

    def test_short_http_body_is_uncertain_even_if_the_prefix_is_valid_json(
        self,
    ) -> None:
        raw = json.dumps(self.api_body()).encode()
        execution, result, _ = self.worker(raw=raw, advertised_length=str(len(raw) + 1))
        self.assertFalse(result["complete"])
        with self.assertRaises(ModelUnresolved):
            self.provider.decode("respond", execution)

    def test_missing_credential_child_fails_without_a_network_attempt(self) -> None:
        with patch.dict(
            "os.environ", {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}, clear=True
        ):
            execution = self.provider.execute(self.request, timeout=2)
        with self.assertRaises(ProviderError):
            self.provider.decode("respond", execution)
        self.assertTrue(execution["complete"])

    def test_cost_is_unknown_without_rates_or_if_the_response_model_changes(
        self,
    ) -> None:
        provider = OpenAIProvider(
            self.root / "artifacts", model="gpt-4.1-mini-2025-04-14"
        )
        self.assertIsNone(
            require_value(
                provider.usage(self.worker()[0]), "Expected decoded provider usage"
            )["estimated_cost_micro_usd"]
        )
        self.assertIsNone(
            require_value(
                self.provider.usage(
                    self.worker(body=self.api_body(model="different-model"))[0]
                ),
                "Expected decoded provider usage",
            )["estimated_cost_micro_usd"]
        )
        self.assertIsNone(
            self.provider.usage(self.worker(body=self.api_body(usage=None))[0])
        )

    def test_request_policy_refuses_oversized_context_before_dispatch(self) -> None:
        bundle = dict(self.bundle, extra="x" * 65536)
        with self.assertRaises(ProviderError):
            self.provider.prepare("respond", bundle)

    def test_cost_estimates_require_the_served_default_tier(self) -> None:
        self.assertEqual(self.request["body"].get("service_tier"), "default")
        self.assertEqual(
            require_value(
                self.provider.usage(
                    self.worker(body=self.api_body(service_tier="default"))[0]
                ),
                "Expected decoded provider usage",
            )["estimated_cost_micro_usd"],
            58,
        )
        for tier in ("priority", "flex", "auto", None):
            with self.subTest(tier=tier):
                usage = self.provider.usage(
                    self.worker(body=self.api_body(service_tier=tier))[0]
                )
                assert usage is not None, "Expected decoded usage for the served tier"
                self.assertEqual(usage["input_tokens"], 120)
                self.assertIsNone(usage["estimated_cost_micro_usd"])
        missing = self.api_body()
        missing.pop("service_tier", None)
        self.assertIsNone(
            require_value(
                self.provider.usage(self.worker(body=missing)[0]),
                "Expected decoded provider usage",
            )["estimated_cost_micro_usd"]
        )

    def test_timeout_policy_keeps_the_exact_integer_milliseconds(self) -> None:
        for milliseconds in (1, 1005, 16001, 119999, 120000):
            with self.subTest(milliseconds=milliseconds):
                provider = OpenAIProvider(
                    self.root / "artifacts",
                    model=self.provider.model,
                    timeout_ms=milliseconds,
                    pricing=self.pricing,
                )
                request = provider.prepare("respond", self.bundle)
                self.assertEqual(request["timeout_ms"], milliseconds)
                policy = {
                    "model": provider.model,
                    "max_output_tokens": 2048,
                    "max_request_bytes": 65536,
                    "timeout_ms": milliseconds,
                    "pricing": self.pricing,
                }
                self.assertEqual(provider.identity["policy_hash"], digest(policy))

    def test_cost_rounds_up_a_sub_micro_dollar_remainder(self) -> None:
        body = self.api_body(
            usage={
                "input_tokens": 121,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 10,
                "total_tokens": 131,
            }
        )
        self.assertEqual(
            require_value(
                self.provider.usage(self.worker(body=body)[0]),
                "Expected decoded provider usage",
            )["estimated_cost_micro_usd"],
            59,
        )
        fixture = CONTRACTS_ROOT / "spec" / "conformance"
        recorded = json.loads((fixture / "openai-result-ceil.json").read_text())
        execution = {
            "profile": "remote-agent/process-result/1",
            "stdout": base64.b64encode(canonical(recorded)).decode(),
            "stderr": "",
            "exit_code": 0,
            "failure": None,
            "complete": True,
            "elapsed_ms": 1,
        }
        expected = json.loads((fixture / "openai-usage-ceil.json").read_text())
        self.assertEqual(expected["estimated_cost_micro_usd"], 59)
        self.assertEqual(self.provider.usage(execution), expected)

    def test_service_configuration_accepts_native_provider_and_rejects_endpoint_or_key_fields(
        self,
    ) -> None:
        config = {
            "profile": "band-contract/service/1",
            "kind": "agent",
            "engine": {
                "command": ["/usr/bin/true"],
                "contract": str(self.root / "contract.band"),
            },
            "store": {"path": str(self.root / "service/state.sqlite3")},
            "http": {
                "host": "127.0.0.1",
                "port": 0,
                "tokens_file": str(self.root / "tokens.json"),
            },
            "agent": {
                "actor": "agent",
                "program_source": self.artifact.path,
                "artifact_directory": str(self.root / "artifacts"),
                "room": {
                    "url": "https://room.example.invalid",
                    "token_file": str(self.root / "room.token"),
                },
                "provider": {
                    "kind": "openai",
                    "model": "gpt-4.1-mini-2025-04-14",
                    "pricing": self.pricing,
                },
            },
        }
        path = self.root / "service.json"
        path.write_bytes(canonical(config))
        path.chmod(0o600)
        loaded = load_config(path)
        self.assertEqual(loaded["agent"]["provider"]["max_output_tokens"], 2048)
        self.assertEqual(loaded["agent"]["provider"]["max_request_bytes"], 65536)
        for key, value in [
            ("api_key", "never-in-config"),
            ("endpoint", "http://remote.invalid"),
            ("max_output_tokens", 1000000),
        ]:
            changed = json.loads(json.dumps(config))
            changed["agent"]["provider"][key] = value
            path.write_bytes(canonical(changed))
            with self.assertRaises(ConfigurationError):
                load_config(path)
