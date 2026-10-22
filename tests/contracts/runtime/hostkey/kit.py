from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import tests.contracts.runtime.hybrid as _hybrid
from remote_agent.hostkey import HostKey
from remote_agent.store import Store


class HostKeyLifecycleFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h7-private-key-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.close)


class StoredIdRecoveryFixture(_hybrid.HybridFixture):
    def pending(self) -> tuple[Any, ...]:
        agent = self.start()
        event = self.question()
        publish = self.room.publish

        def lose_ack(envelope: Any, actor: Any, now: Any) -> None:
            publish(envelope, actor, now)
            raise RuntimeError("ack lost")

        with patch.object(self.room, "publish", lose_ack):
            self.assertEqual(agent.receive(event)["status"], "held")
        return agent, event, self.store.get("staged")["envelope"]["id"]

    def restart(self, agent: Any, event: Any, identifier: Any) -> None:
        agent.close()
        self.store.close()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        restarted = self.start()
        with patch.object(
            HostKey,
            "identifier",
            side_effect=AssertionError("Recovery rederived an ID"),
        ):
            reply = restarted.receive(event)
        self.assertEqual(reply["status"], "completed")
        self.assertEqual(reply["receipt"]["id"], identifier)
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.store.get("turns"), 1)
