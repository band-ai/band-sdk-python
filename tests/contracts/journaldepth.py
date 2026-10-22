"""Retain real callback records and expose their native journal response shape."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import httpx

from band.contracts.wire import JsonObject
from remote_agent.invocations import InvocationJournal
from remote_agent.program import Artifact, Program
from remote_agent.protocol import decode
from remote_agent.store import Store
from tests.contracts.programs.peer import ProgramPeer
from tests.contracts.runtime.setql8 import nested
from tests.contracts.runtime.setqq2 import capture


@dataclass(frozen=True)
class RecordedJournal:
    path: Path
    page: JsonObject


def record_callback(root: Path, levels: int) -> RecordedJournal:
    """Journal an accepted boundary callback while replacing only its execution."""
    state = nested(levels)
    reply: JsonObject = {"type": "handled", "state": state, "message": None}
    assert decode(json.dumps(reply)) == reply
    path = root / "agent.sqlite3"
    with Store(path) as store:
        program = Program(
            Artifact.publish("# recorded-only fixture\n", root / "artifacts"),
            journal=InvocationJournal(store),
        )
        with patch.object(program, "execute", return_value=capture(reply)):
            assert program.handle({}, state, {}, invocation_id="deep") == reply
        page = store.search()
    return RecordedJournal(path, page)


def record_private_value(root: Path) -> RecordedJournal:
    """Retain accepted private JSON outside the callback input domain."""
    path = root / "private.sqlite3"
    with Store(path) as store:
        store.commit("private_value", nested(256))
        page = store.search()
    return RecordedJournal(path, page)


class JournalPeer(ProgramPeer):
    def __init__(self, page: JsonObject) -> None:
        super().__init__()
        self.page = page

    def handle(self, request: httpx.Request) -> httpx.Response:
        response = super().handle(request)
        body: JsonObject = response.json()
        body.update(
            available=True,
            assessment_authors=[],
            assessment=None,
            runtime={
                "id": "recorded-program",
                "status": "running",
                "hold": None,
                "role": "participant",
                "actor": "Agent:participant",
                "journal": True,
                "mode": "program",
            },
            journal_page=self.page,
            replay=None,
            control=None,
            task=None,
        )
        return httpx.Response(200, json=body)


def assert_journal_request(peer: JournalPeer) -> None:
    """Require the unchanged authenticated, scoped journal operation."""
    assert peer.requests == [
        {
            "profile": "band-contract/program-host-request/1",
            "room": "room",
            "session": "session",
            "actor": "User:owner",
            "context": {},
            "operation": "journal",
            "request": {"after": 0, "limit": 50, "query": ""},
        }
    ]
