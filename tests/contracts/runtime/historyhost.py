"""Route public HTTP requests through the real program-host journal operation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Never

import httpx

from band.contracts.models import JournalQuery
from band.contracts.programs import ContractProgramHostClient, ProgramScope
from remote_agent.band.host import ProgramHost
from remote_agent.store import Store
from remote_agent.types import JsonObject
from tests.contracts.programs.peer import ProgramPeer
from tests.contracts.runtime.bandprogramhost import DurableInstance


def unused_assessor(context: JsonObject, request: JsonObject) -> Never:
    raise AssertionError("Reading a retained journal must not assess a program")


def unused_factory(value: JsonObject, assessment: JsonObject, root: Path) -> Never:
    raise AssertionError("Reading a retained journal must not start a participant")


class StoredParticipant(DurableInstance):
    def __init__(self, store: Store) -> None:
        self.store = store

    def journal(self, **page: Any) -> JsonObject:
        return self.store.search(**page)


class HostJournalPeer(ProgramPeer):
    def __init__(self, host: ProgramHost) -> None:
        super().__init__()
        self.scope = ProgramScope(
            room="room", session="s", actor="User:owner", context={}
        )
        self.host = host
        self.response: JsonObject | None = None

    def handle(self, request: httpx.Request) -> httpx.Response:
        super().handle(request)
        status, body = self.host.dispatch(
            request.method,
            request.url.path,
            {"actor": "BandPlatform", "permissions": ["controls"]},
            self.requests[-1],
        )
        self.response = body
        return httpx.Response(status, json=body)


async def read_host(peer: HostJournalPeer) -> JsonObject:
    async with (
        peer.client() as http,
        ContractProgramHostClient(
            peer.connection, scope=peer.scope, http=http
        ) as client,
    ):
        return await client.journal(JournalQuery(kind="model_input"))
