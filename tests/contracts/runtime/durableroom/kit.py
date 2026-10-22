from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.authority import DurableRoom
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, TransportError
from tests.paths import CONTRACTS_ROOT


class DurableRoomFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="durable-room-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        self.clock = 1_790_000_000_000
        self.engines = []
        self.stores = []
        self.addCleanup(self.cleanup)
        self.start()

    def cleanup(self) -> None:
        for engine in self.engines:
            engine.close()
        for store in self.stores:
            store.close()

    def start(self, **options: Any) -> Any:
        self.engine = EngineProcess(self.contract)
        self.engines.append(self.engine)
        self.store = Store(self.root / "room.sqlite3")
        self.stores.append(self.store)
        bindings = options.pop("bindings", {"requester": "user", "assistant": "agent"})
        self.room = DurableRoom(
            self.store,
            self.engine,
            session="s",
            bindings=bindings,
            clock=lambda: self.clock,
            snapshot_every=2,
            **options,
        )
        return self.room

    def restart(self, **options: Any) -> Any:
        self.engine.close()
        self.store.close()
        return self.start(**options)

    def question(self) -> Any:
        self.message = {
            "id": "question-1",
            "session": "s",
            "kind": "question",
            "payload": {"text": "private accepted input"},
            "expected_version": 0,
        }
        return self.room.publish(self.message, "user", self.clock)


REPLIES = (
    "---\nname: deploy\ndescription: Ok and error replies.\nengine: 0.2.0-lab.1\n"
    "roles: [lead, deployer]\n---\ncontract @1h => expired\ntype deploy\ntype ok\n"
    "  request: ref(deploy)\nstate new\n  lead -> deployer: deploy() as job awaits deploying\n"
    "    => ok(request=$job) & deployed\n     | @10m & stalled\n"
    "end deployed\nend stalled\n"
)


class DurableReplyRoomFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="durable-replies-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.contract = self.root / "deploy.band"
        self.contract.write_text(REPLIES)
        self.clock = 1_790_000_000_000
        self.opened = []
        self.addCleanup(lambda: [item.close() for item in self.opened])

    def open(self, **options: Any) -> tuple[Any, ...]:
        engine, store = EngineProcess(self.contract), Store(self.root / "room.sqlite3")
        self.opened += [engine, store]
        return (
            DurableRoom(
                store,
                engine,
                session="s",
                bindings={"lead": "l", "deployer": "d"},
                clock=lambda: self.clock,
                snapshot_every=1,
                **options,
            ),
            engine,
            store,
        )

    def refused(self, pattern: Any, **options: Any) -> None:
        with self.assertRaisesRegex(TransportError, pattern):
            self.open(**options)
        self.opened.pop().close()  # the store; the room already closed its engine
