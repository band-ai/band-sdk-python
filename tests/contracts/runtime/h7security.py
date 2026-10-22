from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from remote_agent import operations
from remote_agent.controller import HybridAgent
from remote_agent.hostkey import HostKey
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from remote_agent.transport import (
    EngineProcess,
    LocalGate,
    ReferenceRoom,
    TransportError,
)
from tests.contracts.runtime.hybrid import ScriptedModel
from tests.contracts.runtime.program import SOURCE
from tests.paths import CONTRACT_RUNTIME_TESTS


class H7PresendInterrupted(BaseException):
    """A staged pre-dispatch crash bypasses ordinary held/retry handling."""


class H7SecurityFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract = CONTRACT_RUNTIME_TESTS / "fixtures/h7-preclaim.band"
        cls.authority = EngineProcess(contract, format="frontmatter")
        cls.checker = EngineProcess(contract, format="frontmatter")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.authority.close()
        cls.checker.close()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="h7-independent-")
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)
        self.artifact = Artifact.publish(SOURCE, self.path / "artifacts")
        self.room = self.new_room()
        self.provider = ScriptedModel()
        self.store = self.open_store("agent.sqlite3")

    def new_room(self) -> Any:
        return ReferenceRoom(
            self.authority,
            session="s",
            bindings={"requester": "user", "assistant": "agent", "peer": "peer"},
        )

    def open_store(self, name: Any) -> Any:
        store = Store(self.path / name)
        self.addCleanup(store.close)
        return store

    def start(self) -> Any:
        agent = HybridAgent(
            self.store,
            Program(self.artifact),
            self.room,
            LocalGate(self.checker),
            self.provider,
            actor="agent",
            artifact_directory=self.path / "artifacts",
            now=lambda: 0,
            max_repairs=0,
        )
        self.addCleanup(agent.close)
        return agent

    def question(self, text: str = "ping") -> Any:
        return self.room.publish(
            {
                "id": "input:q",
                "session": "s",
                "kind": "question",
                "payload": {"text": text},
                "expected_version": self.room.view("user", 0)["view"]["version"],
            },
            "user",
            0,
        )

    def staged_recovery(self, change: Any) -> None:
        agent = self.start()
        event = self.question()
        publish = self.room.publish

        def lost_ack(message: Any, actor: Any, now: Any) -> None:
            publish(message, actor, now)
            raise TransportError("Injected lost acknowledgement")

        with patch.object(self.room, "publish", side_effect=lost_ack):
            self.assertEqual(agent.receive(event)["status"], "held")
        staged = self.store.get("staged")
        identifier = staged["envelope"]["id"]
        agent.close()
        self.store.close()
        key_path = Path(str(self.store.path) + ".host-key")
        if change == "rotate":
            operations.rotate_host_key(self.store.path)
        else:
            self.assertTrue(
                key_path.is_file(), "Host key was never privately persisted"
            )
            key_path.unlink()
        self.store = self.open_store("agent.sqlite3")
        agent = self.start()
        fresh = HostKey(self.store).identifier("s", "agent", "input:q", 1)
        self.assertNotEqual(
            fresh, identifier, "The recovery fixture did not change the host key"
        )
        with patch.object(
            HostKey,
            "identifier",
            side_effect=AssertionError("Recovery must never rederive a staged ID"),
        ):
            result = agent.recover()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["receipt"]["id"], identifier)
        self.assertEqual(agent.receive(event), result)
        self.assertEqual(self.store.get("turns"), 1)
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(
            len(
                [record for record in self.store.records() if record["kind"] == "stage"]
            ),
            1,
        )

    def staged_unsent_recovery(self, change: Any) -> None:
        agent = self.start()
        event = self.question()
        # Stage is durably committed first; interrupt BEFORE _dispatch can make
        # a publish_attempt or call the authority. No acknowledgement is lost.
        with (
            patch.object(agent, "_dispatch", side_effect=H7PresendInterrupted()),
            self.assertRaises(H7PresendInterrupted),
        ):
            agent.receive(event)
        staged = self.store.get("staged")
        self.assertIsNotNone(staged)
        self.assertEqual(staged["sends"], 0)
        envelope = staged["envelope"]
        self.assertEqual(self.room.view("agent", 0)["view"]["version"], 1)
        self.assertEqual(
            sum(record["kind"] == "publish_attempt" for record in self.store.records()),
            0,
        )
        program_calls = sum(
            record["kind"] == "program_input" for record in self.store.records()
        )
        agent.close()
        self.store.close()
        if change == "rotate":
            operations.rotate_host_key(self.store.path)
        else:
            key_path = Path(str(self.store.path) + ".host-key")
            self.assertTrue(key_path.is_file(), "Ephemeral key must exist before loss")
            key_path.unlink()
        self.store = self.open_store("agent.sqlite3")
        agent = self.start()
        fresh = HostKey(self.store).identifier("s", "agent", "input:q", 1)
        self.assertNotEqual(
            fresh, envelope["id"], "The fixture must really change the host key"
        )
        lookup = self.room.lookup
        replayed = []

        def record_lookup(message: Any, actor: Any, now: Any) -> Any:
            replayed.append(message)
            return lookup(message, actor, now)

        with (
            patch.object(
                HostKey,
                "identifier",
                side_effect=AssertionError(
                    "Unsent staged recovery must never rederive an ID"
                ),
            ),
            patch.object(self.room, "lookup", side_effect=record_lookup),
        ):
            result = agent.recover()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(replayed, [envelope])
        self.assertEqual(result["receipt"]["id"], envelope["id"])
        self.assertEqual(result["receipt"]["payload"], envelope["payload"])
        self.assertEqual(agent.receive(event), result)
        self.assertEqual(self.store.get("turns"), 1)
        self.assertEqual(self.store.get("state"), {"count": 1})
        self.assertEqual(self.store.get("attempts:" + event["id"]), 1)
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(
            sum(record["kind"] == "program_input" for record in self.store.records()),
            program_calls,
        )
        self.assertEqual(
            sum(record["kind"] == "stage" for record in self.store.records()), 1
        )
        self.assertEqual(
            sum(record["kind"] == "publish_attempt" for record in self.store.records()),
            1,
        )
        self.assertEqual(self.room.view("agent", 0)["view"]["version"], 2)
