from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.authority import DurableRoom
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
from tests.paths import CONTRACTS_ROOT


class RejectionAuditFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        self.now = 1_790_000_000_000
        self.addCleanup(self.close)
        self.start()

    def start(self) -> None:
        self.store = Store(self.root / "room.sqlite3")
        self.engine = EngineProcess(self.contract)
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="s",
            bindings={"requester": "user", "assistant": "agent"},
            clock=lambda: self.now,
            snapshot_every=2,
        )

    def close(self) -> None:
        self.engine.close()
        self.store.close()

    def restart(self) -> None:
        self.close()
        self.start()

    def message(
        self, identifier: str = "bad", text: str | bool = False, version: int = 0
    ) -> dict[str, Any]:
        return {
            "id": identifier,
            "session": "s",
            "kind": "question",
            "payload": {"text": text},
            "expected_version": version,
        }

    def rows(self) -> list[Any]:
        return [
            frame for frame in self.store.records() if frame["kind"] == "room_rejection"
        ]
