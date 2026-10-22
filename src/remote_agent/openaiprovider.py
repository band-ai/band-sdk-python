"""Responses API adapter with exact prepared input and raw bounded result capture."""

from __future__ import annotations

import base64
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Never

from remote_agent.commands import CommandError, result_from_capture
from remote_agent.environment import minimal_environment
from remote_agent.modeljournal import ModelUnresolved
from remote_agent.protocol import decode
from remote_agent.providers import CommandProvider, ProviderError
from remote_agent.settings import runner_settings
from remote_agent.storage import StoreError, canonical, digest
from remote_agent.types import JsonObject, SourcePath


class OpenAIProvider(CommandProvider):
    def __init__(
        self,
        artifact_directory: SourcePath,
        *,
        model: str,
        max_output_tokens: int = 2048,
        max_request_bytes: int = 65_536,
        timeout_ms: int = 30_000,
        pricing: JsonObject | None = None,
    ) -> None:
        self.validate_options(
            model=model,
            max_output_tokens=max_output_tokens,
            max_request_bytes=max_request_bytes,
            timeout_ms=timeout_ms,
            pricing=pricing,
        )
        bootstrap = "import sys; sys.path.insert(0, sys.argv[1]); from remote_agent.openai_worker import main; main()"
        super().__init__(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                bootstrap,
                str(Path(__file__).resolve().parent.parent),
            ],
            artifact_directory,
            timeout=timeout_ms / 1000,
        )
        self.timeout_ms = timeout_ms
        self.model, self.max_output_tokens, self.max_request_bytes = (
            model,
            max_output_tokens,
            max_request_bytes,
        )
        self.pricing = dict(pricing) if pricing is not None else None
        policy = {
            "model": model,
            "max_output_tokens": max_output_tokens,
            "max_request_bytes": max_request_bytes,
            "timeout_ms": timeout_ms,
            "pricing": self.pricing,
        }
        self.identity = {
            "profile": "remote-agent/openai-provider/1",
            "model": model,
            "policy_hash": digest(policy),
            "prompt_hash": self.identity["prompt_hash"],
        }

    def _environment(self) -> dict[str, str]:
        environment = minimal_environment()
        credential = runner_settings().openai_api_key.get_secret_value()
        if credential:
            environment["OPENAI_API_KEY"] = credential
        return environment

    @staticmethod
    def validate_options(
        *,
        model: str,
        max_output_tokens: int = 2048,
        max_request_bytes: int = 65_536,
        timeout_ms: int = 30_000,
        pricing: JsonObject | None = None,
    ) -> None:
        if (
            not isinstance(model, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}", model)
            or type(max_output_tokens) is not int
            or not 16 <= max_output_tokens <= 8192
            or type(max_request_bytes) is not int
            or not 1024 <= max_request_bytes <= 262_144
            or type(timeout_ms) is not int
            or not 1 <= timeout_ms <= 120_000
        ):
            raise ProviderError("Invalid OpenAI provider policy")
        if pricing is not None:
            rates = {
                "input_nano_usd_per_token",
                "cached_input_nano_usd_per_token",
                "output_nano_usd_per_token",
            }
            if (
                not isinstance(pricing, dict)
                or set(pricing) != rates | {"as_of"}
                or any(
                    type(pricing[k]) is not int or not 0 <= pricing[k] <= 100_000
                    for k in rates
                )
                or pricing["cached_input_nano_usd_per_token"]
                > pricing["input_nano_usd_per_token"]
                or not isinstance(pricing["as_of"], str)
                or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", pricing["as_of"])
            ):
                raise ProviderError("Invalid OpenAI price estimate policy")
            date.fromisoformat(pricing["as_of"])

    def prepare(self, operation: str, bundle: JsonObject) -> JsonObject:
        if operation not in ("respond", "wake", "repair"):
            raise ProviderError("Unsupported model operation")
        command = super().prepare(operation, bundle)
        prompt = command.pop("system_prompt")
        body = {
            "model": self.model,
            "instructions": prompt,
            "input": canonical(command).decode("utf-8"),
            "text": {"format": {"type": "json_object"}},
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "stream": False,
            "truncation": "disabled",
            "service_tier": "default",
        }
        if len(canonical(body)) > self.max_request_bytes:
            raise ProviderError("OpenAI request exceeds the configured byte limit")
        return {
            "profile": "remote-agent/openai-request/1",
            "body": body,
            "timeout_ms": self.timeout_ms,
            "max_response_bytes": 131_072,
            "pricing": self.pricing,
        }

    @staticmethod
    def _response(execution: JsonObject) -> JsonObject:
        result = result_from_capture(execution)
        if (
            set(result)
            != {
                "profile",
                "status",
                "request_id",
                "body",
                "complete",
                "failure",
                "uncertain",
                "redacted",
            }
            or result["profile"] != "remote-agent/openai-result/1"
            or any(
                type(result[k]) is not bool
                for k in ("complete", "uncertain", "redacted")
            )
        ):
            raise ProviderError("Invalid OpenAI worker result")
        if result["uncertain"]:
            raise ModelUnresolved(
                "OpenAI outcome is uncertain; reconcile before retrying"
            )
        if result["failure"] or not result["complete"]:
            raise ProviderError(
                "OpenAI operation failed: " + str(result["failure"])[:160]
            )
        if type(result["status"]) is not int or not 200 <= result["status"] < 300:
            raise ProviderError("OpenAI did not return a successful response")
        raw = base64.b64decode(result["body"], validate=True)
        if len(raw) > 131_072:
            raise ProviderError("OpenAI response capacity exceeded")

        def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("Duplicate provider JSON field")
                value[key] = item
            return value

        def nonfinite(_: str) -> Never:
            raise ValueError("Nonfinite provider JSON")

        # Provider metadata may contain floats. The generated business JSON is
        # decoded separately with the runtime's stricter integer-only codec.
        value = json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
        if not isinstance(value, dict):
            raise ProviderError("Invalid OpenAI response object")
        return value

    def decode(self, operation: str, execution: JsonObject) -> JsonObject:
        try:
            response = self._response(execution)
            if response.get("status") != "completed":
                raise ProviderError("OpenAI response was not completed")
            output = response.get("output")
            if not isinstance(output, list) or any(
                not isinstance(item, dict)
                or item.get("type") not in ("message", "reasoning")
                for item in output
            ):
                raise ProviderError("Unexpected OpenAI response items")
            messages = [item for item in output if item.get("type") == "message"]
            if len(messages) != 1 or messages[0].get("role") != "assistant":
                raise ProviderError("Expected one OpenAI assistant message")
            content = messages[0].get("content")
            if (
                not isinstance(content, list)
                or not content
                or any(
                    not isinstance(item, dict)
                    or item.get("type") != "output_text"
                    or not isinstance(item.get("text"), str)
                    for item in content
                )
            ):
                raise ProviderError("OpenAI refused or returned no text result")
            result = decode("".join(item["text"] for item in content).encode("utf-8"))
            if not isinstance(result, dict) or "error" in result:
                raise ProviderError("OpenAI did not return a usable operation result")
            return self._repair_result(result) if operation == "repair" else result
        except ModelUnresolved:
            raise
        except (
            CommandError,
            StoreError,
            OSError,
            ValueError,
            KeyError,
            TypeError,
            RecursionError,
        ) as error:
            raise ProviderError("Invalid or unavailable OpenAI result") from error

    def usage(self, execution: JsonObject) -> JsonObject | None:
        try:
            response = self._response(execution)
            usage = response["usage"]
            incoming, cached, outgoing = (
                usage["input_tokens"],
                usage["input_tokens_details"]["cached_tokens"],
                usage["output_tokens"],
            )
            if (
                any(
                    type(n) is not int or not 0 <= n <= 2_097_152
                    for n in (incoming, cached, outgoing)
                )
                or cached > incoming
            ):
                return None
            cost = None
            if (
                self.pricing
                and response.get("model") == self.model
                and response.get("service_tier") == "default"
            ):
                rates = self.pricing
                nanos = (
                    (incoming - cached) * rates["input_nano_usd_per_token"]
                    + cached * rates["cached_input_nano_usd_per_token"]
                    + outgoing * rates["output_nano_usd_per_token"]
                )
                cost = (
                    nanos + 999
                ) // 1000  # Round each estimate up by less than one micro-USD.
            return {
                "input_tokens": incoming,
                "cached_input_tokens": cached,
                "output_tokens": outgoing,
                "estimated_cost_micro_usd": cost,
            }
        except (
            CommandError,
            ProviderError,
            ModelUnresolved,
            KeyError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            return None  # Explicit unreported usage; never imply zero provider cost.
