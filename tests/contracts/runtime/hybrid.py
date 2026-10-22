from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from remote_agent.controller import HybridAgent
from remote_agent.interfaces import ModelProvider
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from remote_agent.transport import EngineProcess, LocalGate, ReferenceRoom
from tests.contracts.runtime.program import SOURCE
from tests.paths import CONTRACTS_ROOT


class ScriptedModel:
    def __init__(self) -> None:
        self.calls, self.bundles = [], []
        self.repair_started = threading.Event()
        self.release_repair = threading.Event()
        self.candidate: Artifact | None = None
        self.failure = False
        self.invalid = False

    def respond(self, bundle: Any) -> dict[str, Any]:
        self.calls.append("respond")
        self.bundles.append(bundle)
        if self.failure:
            raise RuntimeError("model unavailable")
        event, state = bundle["input"], bundle["state"]
        text = "pong" if event["payload"]["text"] == "ping" else "A considered answer."
        return {
            "state": {"count": state["count"] + 1},
            "scope": "turn",
            "message": {
                "kind": "question" if self.invalid else "answer",
                "payload": {"request": event["id"], "text": text},
            },
        }

    def repair(self, bundle: Any) -> dict[str, Any]:
        self.calls.append("repair")
        self.bundles.append(bundle)
        self.repair_started.set()
        if not self.release_repair.wait(5):
            raise RuntimeError("test repair was not released")
        assert self.candidate is not None, "The test must publish a repair candidate"
        return {"artifact": self.candidate.to_dict(), "resume": True}

    def wake(self, bundle: Any) -> dict[str, Any]:
        self.calls.append("wake")
        self.bundles.append(bundle)
        return {"action": "none", "note": "Inspected the recorded state."}


class HybridFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract = CONTRACTS_ROOT / "contract-lab/contracts/hybrid-assistant.band"
        cls.authority = EngineProcess(contract)
        cls.checker = EngineProcess(contract)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.authority.close()
        cls.checker.close()

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hybrid-agent-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.room = ReferenceRoom(
            self.authority,
            session="s",
            bindings={"requester": "user", "assistant": "agent"},
        )
        self.gate = LocalGate(self.checker)
        self.provider = ScriptedModel()
        self.store = Store(self.path / "agent.sqlite3")
        self.addCleanup(self.store.close)
        self.initial = Artifact.publish(SOURCE, self.path / "artifacts")
        self.agent: HybridAgent | None = None

    def start(
        self,
        source: Any = None,
        *,
        provider: ModelProvider | None = None,
        **options: Any,
    ) -> Any:
        artifact = (
            self.initial
            if source is None
            else Artifact.publish(source, self.path / "artifacts")
        )
        self.agent = HybridAgent(
            self.store,
            Program(artifact, timeout=0.4),
            self.room,
            self.gate,
            self.provider if provider is None else provider,
            actor="agent",
            now=lambda: 0,
            snapshot_every=2,
            artifact_directory=self.path / "artifacts",
            **options,
        )
        self.addCleanup(self.agent.close)
        return self.agent

    def question(self, text: str = "ping", identifier: Any = None) -> Any:
        version = self.room.view("user", 0)["view"]["version"]
        return self.room.publish(
            {
                "id": identifier or f"q{version}",
                "session": "s",
                "kind": "question",
                "payload": {"text": text},
                "expected_version": version,
            },
            "user",
            0,
        )
