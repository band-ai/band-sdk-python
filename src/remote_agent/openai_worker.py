"""Trusted, killable HTTPS worker. The API key never enters the prepared request."""

from __future__ import annotations

import base64
import http.client
import re
import ssl
import sys
from collections.abc import Callable

from remote_agent.protocol import decode
from remote_agent.settings import runner_settings
from remote_agent.storage import canonical
from remote_agent.types import JsonObject


def run(
    request: JsonObject,
    *,
    api_key: str | None = None,
    connection_factory: Callable[
        ..., http.client.HTTPSConnection
    ] = http.client.HTTPSConnection,
) -> JsonObject:
    result = {
        "profile": "remote-agent/openai-result/1",
        "status": None,
        "request_id": None,
        "body": "",
        "complete": False,
        "failure": None,
        "uncertain": False,
        "redacted": False,
    }
    connection = None
    attempted = False
    key = (
        api_key
        if api_key is not None
        else runner_settings().openai_api_key.get_secret_value()
    )
    try:
        if (
            not isinstance(request, dict)
            or set(request)
            != {"profile", "body", "timeout_ms", "max_response_bytes", "pricing"}
            or request["profile"] != "remote-agent/openai-request/1"
            or type(request["timeout_ms"]) is not int
            or not 1 <= request["timeout_ms"] <= 120_000
            or request["max_response_bytes"] != 131_072
        ):
            raise ValueError("Invalid prepared request")
        body = request["body"]
        if (
            set(body)
            != {
                "model",
                "instructions",
                "input",
                "text",
                "store",
                "stream",
                "truncation",
                "max_output_tokens",
                "service_tier",
            }
            or body["store"] is not False
            or body["stream"] is not False
            or body["truncation"] != "disabled"
            or body["service_tier"] != "default"
            or body["text"] != {"format": {"type": "json_object"}}
            or not isinstance(body["model"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}", body["model"])
            or not isinstance(body["instructions"], str)
            or not isinstance(body["input"], str)
            or type(body["max_output_tokens"]) is not int
            or not 16 <= body["max_output_tokens"] <= 8192
        ):
            raise ValueError("Invalid request policy")
        encoded = canonical(body)
        if len(encoded) > 262_144:
            raise ValueError("Request capacity")
        if not isinstance(key, str) or not re.fullmatch(
            r"[A-Za-z0-9_~+/.=-]{16,2048}", key
        ):
            result["failure"] = "OpenAI credential is missing or invalid"
            return result
        connection = connection_factory(
            "api.openai.com",
            timeout=request["timeout_ms"] / 1000,
            context=ssl.create_default_context(),
        )
        attempted = True
        connection.request(
            "POST",
            "/v1/responses",
            encoded,
            {
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        response = connection.getresponse()
        result["status"] = response.status
        length = response.getheader("content-length")
        if length is not None and (
            not re.fullmatch(r"[0-9]{1,12}", length) or int(length) > 131_072
        ):
            result.update(failure="OpenAI response length refused", uncertain=True)
            return result
        request_id = response.getheader("x-request-id")
        if request_id:
            result["redacted"] = key in request_id
            result["request_id"] = request_id.replace(key, "[REDACTED_CREDENTIAL]")[
                :160
            ]
        raw = response.read(131_073)
        result["complete"] = len(raw) <= 131_072 and (
            length is None or len(raw) == int(length)
        )
        raw = raw[:131_072]
        result["redacted"] = result["redacted"] or key.encode() in raw
        raw = raw.replace(key.encode(), b"[REDACTED_CREDENTIAL]")
        result["complete"] = result["complete"] and len(raw) <= 131_072
        result["body"] = base64.b64encode(raw[:131_072]).decode("ascii")
        if not result["complete"]:
            result.update(
                failure="OpenAI response is incomplete or exceeds capacity",
                uncertain=True,
            )
        elif not 200 <= response.status < 300:
            result.update(
                failure="OpenAI HTTP " + str(response.status),
                uncertain=response.status >= 500 or response.status == 408,
            )
        return result
    except Exception:  # noqa: BLE001 -- Any post-dispatch transport failure retains uncertainty without leaking credentials.
        # Exception strings can contain request/credential data. Keep only a safe category.
        result.update(
            failure="OpenAI transport failed"
            if attempted
            else "OpenAI request refused",
            uncertain=attempted,
        )
        return result
    finally:
        if connection is not None:
            connection.close()


def main() -> None:
    try:
        encoded = sys.stdin.buffer.read(524_289)
        if len(encoded) > 524_288:
            raise ValueError("Input capacity")
        result = run(decode(encoded))
    except Exception:  # noqa: BLE001 -- The process boundary must redact all malformed-request exceptions.
        result = {
            "profile": "remote-agent/openai-result/1",
            "status": None,
            "request_id": None,
            "body": "",
            "complete": False,
            "failure": "OpenAI request refused",
            "uncertain": False,
            "redacted": False,
        }
    sys.stdout.buffer.write(canonical(result) + b"\n")


if __name__ == "__main__":
    main()
