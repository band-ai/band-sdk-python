from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from remote_agent.controller import HybridAgent
from remote_agent.interfaces import ModelProvider
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from remote_agent.transport import LocalGate, ReferenceRoom


def capture(value: Any) -> dict[str, Any]:
    return {
        "exit_code": 0,
        "failure": None,
        "complete": True,
        "stdout": base64.b64encode(json.dumps(value).encode()).decode(),
        "stderr": "",
        "elapsed_ms": 1,
    }


class StageCapacityFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "agent.sqlite3")
        self.addCleanup(self.store.close)

    def start(self, *, before: Any = None, view_padding: int = 0) -> Any:
        view = {
            "version": 0,
            "status": "active",
            "obligations": [],
            "padding": "v" * view_padding,
        }
        identity = "stage-capacity-contract"
        room = SimpleNamespace(
            identity=identity,
            session="s",
            idempotent=True,
            view=lambda *args: {"view": view},
            accepted_input=lambda event, *args: event,
            publish=Mock(
                side_effect=lambda envelope, actor, now: dict(
                    envelope, status="accepted", actor=actor
                )
            ),
        )
        gate = SimpleNamespace(
            engine=SimpleNamespace(identity=identity),
            check=lambda *args: {"status": "accepted"},
        )
        metadata = {
            "schema": "1",
            "initial": before or {"count": 0},
            "coverage": {"mode": "full", "handles": [], "delegates": []},
        }
        self.provider = SimpleNamespace(
            respond=Mock(
                return_value={
                    "state": {"count": 1},
                    "message": {"kind": "answer", "payload": {"text": "fallback"}},
                }
            )
        )
        artifact = Artifact.publish(
            "# fixture; execution is captured locally\n", self.root / "artifacts"
        )
        with patch.object(Program, "describe", return_value=metadata):
            self.agent = HybridAgent(
                self.store,
                Program(artifact),
                cast(ReferenceRoom, room),
                cast(LocalGate, gate),
                cast(ModelProvider, self.provider),
                actor="agent",
                artifact_directory=self.root / "artifacts",
                max_repairs=0,
                snapshot_every=10,
            )
        self.addCleanup(self.agent.close)
        return self.agent

    def receive(self, result: Any, *, event_padding: int = 0) -> Any:
        event = {
            "id": "q1",
            "actor": "user",
            "kind": "question",
            "payload": {"padding": "e" * event_padding},
        }
        with patch.object(self.agent.program, "execute", return_value=capture(result)):
            return self.agent.receive(event)

    def assert_completed(self, result: Any, producer: Any) -> None:
        self.assertEqual(
            (result["status"], result.get("producer")), ("completed", producer)
        )
        self.assertIsNone(self.store.get("execution"))
        self.assertIsNone(self.store.get("staged"))
        self.assertEqual(self.store.get("queue"), [])
        self.assertEqual(self.agent.recover()["status"], "idle")
