from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from collections.abc import Callable
from functools import partial
from http.client import HTTPSConnection
from pathlib import Path
from typing import Any, cast

from remote_agent.openai_worker import run
from remote_agent.openaiprovider import OpenAIProvider
from remote_agent.program import Artifact
from remote_agent.storage import canonical
from tests.contracts.runtime.program import SOURCE


class OpenAIProviderFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="openai-provider-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.artifact = Artifact.publish(SOURCE, self.root / "artifacts")
        self.pricing = {
            "input_nano_usd_per_token": 400,
            "cached_input_nano_usd_per_token": 100,
            "output_nano_usd_per_token": 1600,
            "as_of": "2026-10-10",
        }
        self.provider = OpenAIProvider(
            self.root / "artifacts",
            model="gpt-4.1-mini-2025-04-14",
            pricing=self.pricing,
        )
        self.bundle = {
            "program": self.artifact.to_dict(),
            "state": {"count": 0},
            "input": {"id": "q1"},
        }
        self.request = self.provider.prepare("respond", self.bundle)
        self.reply = {
            "state": {"count": 1},
            "message": {
                "kind": "answer",
                "payload": {"request": "q1", "text": "answer"},
            },
            "scope": "turn",
        }

    def api_body(self, **changes: Any) -> dict[str, Any]:
        return {
            "id": "resp_test",
            "model": "gpt-4.1-mini-2025-04-14",
            "service_tier": "default",
            "status": "completed",
            "temperature": 1.0,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": json.dumps(self.reply)}
                    ],
                }
            ],
            "usage": {
                "input_tokens": 120,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens": 10,
                "total_tokens": 130,
            },
            **changes,
        }

    def worker(
        self,
        body: Any = None,
        status: int = 200,
        raw: Any = None,
        request_id: str = "req_test",
        advertised_length: Any = None,
    ) -> tuple[Any, ...]:
        calls = []
        encoded = (
            raw if raw is not None else json.dumps(body or self.api_body()).encode()
        )
        reply = RecordedReply(encoded, status, request_id, advertised_length)
        result = run(
            self.request,
            api_key="private-api-credential-sentinel",
            connection_factory=cast(
                Callable[..., HTTPSConnection],
                partial(RecordedConnection, reply, calls),
            ),
        )
        self.assertNotIn("private-api-credential-sentinel", json.dumps(result))
        execution = {
            "profile": "remote-agent/process-result/1",
            "stdout": base64.b64encode(canonical(result)).decode(),
            "stderr": "",
            "exit_code": 0,
            "failure": None,
            "complete": True,
            "elapsed_ms": 1,
        }
        return execution, result, calls


class RecordedReply:
    def __init__(
        self,
        encoded: bytes,
        status: int,
        request_id: str,
        advertised_length: str | None,
    ) -> None:
        self.stream = io.BytesIO(encoded)
        self.status = status
        self.request_id = request_id
        self.advertised_length = advertised_length


class RecordedConnection:
    def __init__(
        self,
        reply: RecordedReply,
        calls: list[RecordedConnection],
        host: str,
        **options: Any,
    ) -> None:
        self.reply = reply
        self.status = reply.status
        self.host, self.options = host, options
        self.sent: tuple[str, str, Any, dict[str, str]] | None = None
        calls.append(self)

    def request(
        self, method: str, path: str, body: Any, headers: dict[str, str]
    ) -> None:
        self.sent = (method, path, body, headers)

    def getresponse(self) -> RecordedConnection:
        return self

    def getheader(self, name: str, default: Any = None) -> Any:
        match name.lower():
            case "content-length":
                return self.reply.advertised_length
            case "x-request-id":
                return self.reply.request_id
            case _:
                return default

    def read(self, count: int) -> bytes:
        return self.reply.stream.read(count)

    def close(self) -> None:
        pass
