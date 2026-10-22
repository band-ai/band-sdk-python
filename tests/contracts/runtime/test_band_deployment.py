from __future__ import annotations

import json
import os
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

from remote_agent.band.author_service import generate, load_config
from remote_agent.band.configuration import load_host_config
from remote_agent.band.modelbridge import invoke
from tests.contracts.runtime.banddeployment import BandDeploymentFixture
from tests.contracts.runtime.commanddeadline import command_deadline
from tests.contracts.runtime.processgate import ProcessGate
from tests.paths import REPO_ROOT


class TestBandDeployment(BandDeploymentFixture):
    @pytest.mark.contract_linux
    def test_gateway_accepts_https_and_non_demo_loopback_ports(self) -> None:
        value = self.host_config()
        for origin in [
            "https://band.example",
            "http://127.0.0.1:4531",
            "http://[::1]:4531",
        ]:
            value["platform_url"] = origin
            result = load_host_config(Path(self.private("host.json", value)))
            self.assertEqual(result["platform_url"], origin)
            self.assertEqual(result["agent_keys"], value["agent_keys"])

    @pytest.mark.contract_linux
    def test_gateway_assessment_deadline_is_operator_configurable_and_bounded(
        self,
    ) -> None:
        value = self.host_config()
        for configured in (60, 150, 600, 900):
            result = load_host_config(
                Path(
                    self.private(
                        "host.json", {**value, "assessment_timeout_seconds": configured}
                    )
                )
            )
            self.assertEqual(result["assessment_timeout_seconds"], configured)
        for invalid in (59, 901, True, 150.5, "600", None):
            with self.assertRaises(ValueError):
                load_host_config(
                    Path(
                        self.private(
                            "host.json",
                            {**value, "assessment_timeout_seconds": invalid},
                        )
                    )
                )

    @pytest.mark.contract_linux
    def test_gateway_rejects_cleartext_remote_origins_and_credential_urls(self) -> None:
        value = self.host_config()
        for origin in [
            "http://band.example",
            "https://user:secret@band.example",
            "https://band.example/path",
            "https://band.example?key=secret",
            "https://band.example#fragment",
        ]:
            value["platform_url"] = origin
            with self.assertRaises(ValueError) as caught:
                load_host_config(Path(self.private("host.json", value)))
            self.assertNotIn(origin, str(caught.exception))

    @pytest.mark.contract_linux
    def test_provider_gets_only_explicit_credentials_and_structured_request(
        self,
    ) -> None:
        config = self.model("""import json,os,sys
request=json.load(sys.stdin)
assert set(request)=={'profile','prompt','schema'}
assert request['prompt']=='Analyze the actual history'
assert request['schema']['required']==['source']
assert os.environ['MODEL_API_KEY']=='model-test-key-not-a-real-credential'
assert 'UNRELATED_SECRET' not in os.environ
print(json.dumps({'source':'a structured result'}))
""")
        schema = {
            "type": "object",
            "required": ["source"],
            "additionalProperties": False,
            "properties": {"source": {"type": "string"}},
        }
        with patch.dict(os.environ, {"UNRELATED_SECRET": "must-not-leak"}):
            result = invoke(
                self.root / "audit",
                "Analyze the actual history",
                schema,
                configuration=config,
                timeout=2,
            )
        self.assertEqual(result, {"source": "a structured result"})
        self.assertFalse(
            (self.root / "audit").exists(),
            "raw prompts must not escape the managed journals",
        )

    @pytest.mark.contract_linux
    def test_provider_schema_failure_has_no_secret_or_provider_output(self) -> None:
        config = self.model(
            'print(\'{"source":"model-test-key-not-a-real-credential"}\')'
        )
        with self.assertRaises(ValueError) as caught:
            invoke(
                self.root / "audit",
                "input",
                {"type": "object", "required": ["missing"]},
                configuration=config,
                timeout=2,
            )
        self.assertNotIn("model-test-key", str(caught.exception))

    @pytest.mark.contract_linux
    def test_model_processes_are_bounded_and_do_not_inherit_shell_configuration(
        self,
    ) -> None:
        gate = ProcessGate()
        self.addCleanup(gate.close)
        config = self.model_command(gate.command())
        with (
            command_deadline(gate.await_entry) as deadline,
            self.assertRaisesRegex(ValueError, "Structured model deadline exceeded"),
        ):
            invoke(
                self.root / "audit",
                "input",
                {"type": "object"},
                configuration=config,
                timeout=10,
            )
        self.assertTrue(deadline.expired)
        gate.await_closed()
        config = self.model('print("x" * 2_000_000)')
        with self.assertRaisesRegex(ValueError, "unavailable or invalid"):
            invoke(
                self.root / "audit",
                "input",
                {"type": "object"},
                configuration=config,
                timeout=10,
            )

    @pytest.mark.contract_linux
    def test_provider_deadline_failure_is_distinguished_from_an_invalid_result(
        self,
    ) -> None:
        gate = ProcessGate()
        self.addCleanup(gate.close)
        config = self.model_command(gate.command())
        with (
            command_deadline(gate.await_entry) as deadline,
            self.assertRaisesRegex(
                ValueError, "Structured model deadline exceeded"
            ) as caught,
        ):
            invoke(
                self.root,
                "private task details",
                {"type": "object"},
                configuration=config,
                timeout=10,
            )
        self.assertTrue(deadline.expired)
        gate.await_closed()
        self.assertNotIn("private task details", str(caught.exception))
        self.assertNotIn("model-test-key", str(caught.exception))

    @pytest.mark.contract_linux
    def test_author_deadline_is_operator_configurable_and_bounded(self) -> None:
        value = {
            "profile": "band-contract/author-service/1",
            "state": str(self.root / "author"),
            "platform_url": "https://band.example",
            "key_file": self.private("author.key", "a" * 32),
            "model_config": self.model('raise RuntimeError("must not execute")'),
        }
        job = {"operation": "refine", "history": []}
        self.assertEqual(
            load_config(
                Path(
                    self.private("author.json", {**value, "model_timeout_seconds": 600})
                )
            )["model_timeout_seconds"],
            600,
        )
        for configured, expected in [(None, 180), (1, 1), (600, 600), (900, 900)]:
            candidate = dict(value)
            if configured is not None:
                candidate["model_timeout_seconds"] = configured
            config = load_config(Path(self.private("author.json", candidate)))
            with patch(
                "remote_agent.band.author_service.invoke", return_value={}
            ) as provider:
                generate(self.root, "PINNED GRAMMAR", job, config)
            self.assertEqual(provider.call_args.kwargs["timeout"], expected)
            self.assertEqual(provider.call_args.kwargs["timeout_limit"], expected)
        for invalid in [0, -1, 901, True, 1.5, "600", None]:
            with self.assertRaises(ValueError):
                load_config(
                    Path(
                        self.private(
                            "author.json", {**value, "model_timeout_seconds": invalid}
                        )
                    )
                )

    @pytest.mark.contract_linux
    def test_extended_model_deadline_requires_an_explicit_bounded_host_limit(
        self,
    ) -> None:
        config = self.model('raise RuntimeError("must not execute")')
        with (
            patch("remote_agent.band.modelbridge.capture") as capture,
            patch("remote_agent.band.modelbridge.result_from_capture", return_value={}),
        ):
            invoke(
                self.root,
                "input",
                {"type": "object"},
                configuration=config,
                timeout=600,
            )
            self.assertEqual(capture.call_args.kwargs["timeout"], 180)
            invoke(
                self.root,
                "input",
                {"type": "object"},
                configuration=config,
                timeout=600,
                timeout_limit=600,
            )
            self.assertEqual(capture.call_args.kwargs["timeout"], 600)
            invoke(
                self.root,
                "input",
                {"type": "object"},
                configuration=config,
                timeout=0.1,
                timeout_limit=600,
            )
            self.assertEqual(capture.call_args.kwargs["timeout"], 0.1)
            for invalid in [0, 901, True, float("inf")]:
                with self.assertRaises(ValueError):
                    invoke(
                        self.root,
                        "input",
                        {"type": "object"},
                        configuration=config,
                        timeout=600,
                        timeout_limit=cast(int, invalid),
                    )

    @pytest.mark.contract_linux
    def test_host_configuration_cannot_override_child_process_loader_environment(
        self,
    ) -> None:
        config = self.model('raise RuntimeError("must not execute")')
        value = json.loads(Path(config).read_text())
        value["credentials"] = {"LD_PRELOAD": self.private("injected", "not a loader")}
        Path(config).write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            invoke(
                self.root / "audit",
                "input",
                {"type": "object"},
                configuration=config,
                timeout=1,
            )

    def test_wheel_declares_both_services_and_bundles_the_authoring_assets(
        self,
    ) -> None:
        source = REPO_ROOT
        metadata = tomllib.loads((source / "pyproject.toml").read_text())
        self.assertEqual(
            metadata["project"]["scripts"]["contract-band-programs"],
            "remote_agent.band.serve_programs:main",
        )
        self.assertEqual(
            metadata["project"]["scripts"]["contract-band-author"],
            "remote_agent.band.author_service:main",
        )
        for name in ["room-protocol-author.md", "replacement.md", "grammar.md"]:
            self.assertGreater(
                len(files("remote_agent.band").joinpath("assets", name).read_text()),
                100,
            )
