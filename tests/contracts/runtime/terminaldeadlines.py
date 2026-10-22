from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Never

import tests.contracts.runtime.bandqualityauthoring as quality_authoring
from remote_agent.controller import HybridAgent
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, LocalGate, ReferenceRoom
from tests.contracts.runtime.bandrolequality import SOURCE
from tests.contracts.runtime.required import require_value
from tests.paths import CONTRACT_RUNTIME_TESTS

CONTRACT = CONTRACT_RUNTIME_TESTS / "fixtures/band/label-normalization.band.txt"


INITIAL = {"processed": 0, "mode": "ascii"}


DONE = {"status": "completed", "producer": "supervisor", "receipt": None}


EXHAUSTED = {"status": "held", "reason": "Response attempt budget exhausted"}


SILENT = SOURCE.replace(
    '    return Handled(next_state,Message("normalized",{"request":event["id"],"text":payload["text"].upper()}))',
    "    return Handled(state)",
).replace("    context.checkpoint(next_state)", "    context.checkpoint(state)")


class UnusedModel:
    def __init__(self) -> None:
        self.calls = []

    def respond(self, bundle: Any) -> Never:
        self.calls.append("respond")
        raise AssertionError("Terminal cleanup does not need a model")

    def repair(self, bundle: Any) -> Never:
        self.calls.append("repair")
        raise AssertionError("A terminal callback is not a repairable program fault")

    def wake(self, bundle: Any) -> Never:
        self.calls.append("wake")
        raise AssertionError("Terminal cleanup does not need a model")


class NoListener:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass


class TerminalDeadlineFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.command = quality_authoring.QualityAuthoringFixture.reference_engine()
        cls.authority = EngineProcess(CONTRACT, command=cls.command)
        cls.checker = EngineProcess(CONTRACT, command=cls.command)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.authority.close()
        cls.checker.close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="terminal-deadline-")
        self.addCleanup(self.temp.cleanup)
        self.root, self.clock = Path(self.temp.name), [0]
        self.room = ReferenceRoom(
            self.authority,
            session="s",
            bindings={"requester": "user", "normalizer": "agent"},
        )
        self.gate, self.provider = LocalGate(self.checker), UnusedModel()
        self.store = Store(self.root / "agent.sqlite3")
        self.addCleanup(self.store.close)

    def start(self, source: Any = SOURCE, *, max_attempts: int = 1) -> Any:
        artifact = Artifact.publish(source, self.root / "artifacts")
        agent = HybridAgent(
            self.store,
            Program(artifact, timeout=0.4),
            self.room,
            self.gate,
            self.provider,
            actor="agent",
            now=lambda: self.clock[0],
            artifact_directory=self.root / "artifacts",
            max_attempts=max_attempts,
            max_repairs=0,
            control_actors={"operator": ["retry"]},
        )
        self.addCleanup(agent.close)
        return agent

    def request(self, identifier: str = "normalize-1") -> Any:
        version = self.room.view("user", self.clock[0])["view"]["version"]
        event = self.room.publish(
            {
                "id": identifier,
                "session": "s",
                "kind": "normalize",
                "payload": {"text": "Band"},
                "expected_version": version,
            },
            "user",
            self.clock[0],
        )
        self.assertEqual(event["status"], "accepted")
        return event

    def expire(
        self, *, now: int = 120000, outcome: str = "timed_out", diagnostics: bool = True
    ) -> Any:
        self.clock[0] = now
        view = self.room.view("agent", now)["view"]
        self.assertEqual(
            (view["status"], view["outcome"], view["legal"]), ("terminal", outcome, [])
        )
        self.assertEqual(bool(view["obligations"]), diagnostics)
        return view

    def assert_finished(
        self, agent: Any, event: Any, view: Any, result: Any, *, attempts: int = 0
    ) -> None:
        self.assertEqual(result, DONE)
        self.assertEqual(self.store.get("queue"), [])
        self.assertIsNone(self.store.get("execution"))
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.store.get("state"), INITIAL)
        self.assertEqual(
            require_value(
                self.store.latest_snapshot(), "Expected a committed snapshot"
            )["state"],
            INITIAL,
        )
        self.assertEqual(self.store.get("turns"), 1)
        self.assertEqual(self.store.get("attempts:" + event["id"], 0), attempts)
        self.assertEqual(self.store.get("model_calls"), 0)
        self.assertEqual(self.provider.calls, [])
        current = self.room.view("agent", self.clock[0])["view"]
        self.assertEqual(current["facts"], view["facts"])
        self.assertEqual(current["obligations"], view["obligations"])
        self.assertEqual(agent.receive(event), DONE)
        self.assertEqual(agent.recover(), {"status": "idle"})
        self.store._verify()
