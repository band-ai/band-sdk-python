"""Program requests preserve platform scope and stable operation identities."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import JsonValue, SecretStr, ValidationError

from band.contracts.models import ContractConnection
from band.contracts.programs import (
    ContractProgramHostClient,
    ProgramControl,
    ProgramScope,
    ProgramTask,
)


class TestOperations:
    async def test_task_retry_keeps_bound_scope_and_original_request(self) -> None:
        received: list[dict] = []

        def peer(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer " + "h" * 32
            received.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "profile": "band-contract/program/1",
                    "room": "room",
                    "session": "session",
                },
            )

        scope = ProgramScope(
            room="room",
            session="session",
            actor="User:owner",
            context={"source": {"sha256": "pinned"}},
        )
        payload: dict[str, JsonValue] = {"a": 1}
        task = ProgramTask(id="stable", kind="question", payload=payload)
        payload["a"] = 9
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(peer)) as http,
            ContractProgramHostClient(
                ContractConnection(
                    url="http://127.0.0.1:8000",
                    credential=SecretStr("h" * 32),
                    authentication="bearer",
                ),
                scope=scope,
                http=http,
            ) as client,
        ):
            await client.task(task)
            await client.task(task)
            await client.control(ProgramControl(id="stop", action="stop"))
            await client.status()
        assert received[0] == received[1]
        assert received[0]["request"] == {
            "id": "stable",
            "kind": "question",
            "payload": {"a": 1},
        }
        assert received[0]["profile"] == "band-contract/program-host-request/1"
        assert received[2]["request"] == {"id": "stop", "action": "stop"}
        assert received[3]["request"] == {}

    def test_unbound_agent_cannot_fabricate_owner_scope(self) -> None:
        with pytest.raises(ValidationError):
            ProgramScope(
                room="room", session="session", actor="Agent:ordinary", context={}
            )
