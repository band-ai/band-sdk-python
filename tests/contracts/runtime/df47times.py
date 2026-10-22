from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.authority import DurableRoom
from remote_agent.network import RoomAPI
from remote_agent.store import Store
from remote_agent.transport import EngineProcess
from tests.paths import CONTRACTS_ROOT


class AcceptedTimesFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = (
            CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        ).read_text()
        source = source.replace(
            "roles: [requester, assistant]", "roles: [requester, assistant, observer]"
        )
        self.contract = self.root / "private.band"
        self.contract.write_text(
            source.replace("type question", "role observer\ntype question", 1)
        )
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
            bindings={
                "requester": "user",
                "assistant": "agent",
                "observer": "observer",
            },
            clock=lambda: self.now,
        )
        self.api = RoomAPI(self.room)

    def close(self) -> None:
        self.engine.close()
        self.store.close()

    def inbox(self, actor: Any) -> Any:
        return self.api.dispatch(
            "POST",
            "/v1/inbox",
            {"actor": actor, "permissions": ["room"]},
            {"after": 0, "limit": 128},
        )[1]
