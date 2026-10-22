from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.receiptwake import WakeJournal
from tests.contracts.runtime.roomcli.kit import RoomCLIFixture


class ReceiptWakeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="receipt-wake-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "wakes.sqlite3"
        self.scope = {
            "endpoint": "http://127.0.0.1:1234",
            "actor": "lead",
            "session": "room-1",
            "identity": {"digest": "contract-1"},
        }
        self.event = {
            "status": "accepted",
            "id": "a" * 32,
            "actor": "lead",
            "session": "room-1",
            "kind": "assign",
            "payload": {"task": "work"},
            "recipients": ["member"],
            "version": 1,
        }

    def journal(self, scope: Any = None) -> Any:
        return WakeJournal(self.path, scope or self.scope)

    def receiver_scope(self) -> dict[str, Any]:
        return dict(self.scope, actor="member")

    def page(self) -> dict[str, Any]:
        return {"cursor": 7, "events": [self.event], "head": {"sequence": 7}}


class ReceiptWakeCLIFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = RoomCLIFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
