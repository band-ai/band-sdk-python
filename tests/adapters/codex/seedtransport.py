"""Real Codex process and REST transport resources for seed boundary tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import httpx

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.client.rest import AsyncRestClient
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import ALICE
from tests.adapters.codex.seedpeer import Step

PARTICIPANT_FAILURE = "participant HTTP fault"


class SeedTransport:
    """Record native/HTTP traffic while controlling only external failures."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.rest: AsyncRestClient
        self.trace = root / "requests.jsonl"
        self.action = root / "outcome.txt"
        self.action.write_text(Step.COMPLETE)
        self.events: list[dict[str, Any]] = []
        self.requests: list[tuple[str, str]] = []
        self.participant_error = False
        self.adapters: list[CodexAdapter] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Accept event posts, participant reads and empty room history."""
        self.requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/events"):
            event = json.loads(request.content)["event"]
            self.events.append(event)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "posted",
                        "message_type": event["message_type"],
                        "success": True,
                    }
                },
            )
        if request.method == "GET" and request.url.path.endswith("/participants"):
            if self.participant_error:
                raise httpx.ConnectError(PARTICIPANT_FAILURE, request=request)
            return httpx.Response(200, json={"data": [ALICE]})
        if request.method == "GET" and request.url.path.endswith("/context"):
            return httpx.Response(
                200,
                json={
                    "data": [],
                    "metadata": {
                        "page": 1,
                        "page_size": 50,
                        "total_count": 0,
                        "total_pages": 0,
                        "has_more": False,
                        "limit": 50,
                    },
                },
            )
        raise AssertionError(f"Unexpected HTTP: {request.method} {request.url}")

    async def start(self, *peer_options: str) -> CodexAdapter:
        adapter = CodexAdapter(
            CodexAdapterConfig(
                model="seed-boundary-model",
                codex_command=(
                    sys.executable,
                    str(Path(__file__).with_name("seedpeer.py")),
                    str(self.trace),
                    str(self.action),
                    *peer_options,
                ),
                workspace_for_room=lambda room: str(self.root),
            ),
            emit=(),
        )
        self.adapters.append(adapter)
        await adapter.on_started("Codex Agent", "A coding agent")
        return adapter

    def native_requests(self, method: CodexRequestMethod) -> list[dict[str, Any]]:
        """Read settled native requests by their supported RPC method."""
        return [
            request["params"]
            for line in self.trace.read_text().splitlines()
            if (request := json.loads(line)).get("method") == method
        ]
