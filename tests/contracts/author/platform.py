"""Serve explicit native author job projections at the platform HTTP boundary."""

from __future__ import annotations

import hashlib

import httpx
from pydantic import SecretStr

from band.contracts.models import ContractConnection
from band.contracts.wire import JsonObject


class AuthorPeer:
    def __init__(self) -> None:
        self.job = "0695bead-070c-4e1d-91d9-b15b4a05bdee"
        self.room = "9abbc918-d8e2-44f0-bc5f-7992a12f7636"
        self.author = "7386ba04-2699-44bd-bbc3-8747df269b2b"
        self.responses: list[JsonObject | None] = []
        self.requests: list[httpx.Request] = []

    def connection(self) -> ContractConnection:
        return ContractConnection(
            url="https://platform.test", credential=SecretStr("a" * 32)
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    def project(
        self,
        *,
        attempt: int,
        status: str,
        result: JsonObject | None = None,
        diagnostics: JsonObject | str | None = None,
    ) -> JsonObject:
        source: JsonObject | None = None
        definition: JsonObject | None = None
        if status in ("ready", "needs_input"):
            assert result is not None
            content = result["source"]
            assert isinstance(content, str)
            digest = hashlib.sha256(content.encode()).hexdigest()
            source = {
                "profile": "band-contract/file-source/1",
                "format": "frontmatter",
                "content": content,
                "sha256": digest,
            }
            definition = {
                "profile": "band-contract/spike-definition/1",
                "name": "correction",
                "description": "A corrected author result",
                "engine": "0.2.0-lab.1",
                "source": digest,
                "definition": "d" * 64,
                "roles": [{"name": "participant", "doc": None}],
                "declarations": {},
            }
        return {
            "profile": "band-contract/authoring-job/1",
            "id": self.job,
            "room": self.room,
            "author_id": self.author,
            "status": status,
            "attempt": attempt,
            "history_count": 1,
            "range": {"from": "2026-10-22T00:00:00Z", "to": "2026-10-22T00:01:00Z"},
            "inserted_at": "2026-10-22T00:01:00Z",
            "goal": "Describe the participant's protocol",
            "operation": "generate",
            "result": result,
            "source": source,
            "definition": definition,
            "baseline": None,
            "diagnostics": diagnostics,
            "claimed_at": None if status == "queued" else "2026-10-22T00:02:00Z",
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert (
            request.url.path == f"/api/v1/agent/contracts/authoring/{self.job}/result"
        )
        assert request.headers["x-api-key"] == "a" * 32
        self.requests.append(request)
        response = self.responses.pop(0)
        if response is None:
            raise httpx.ReadTimeout("The completion response was lost", request=request)
        return httpx.Response(200, json=response)
