from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.band.host import ProgramHost


class Participant:
    durable = True

    def __init__(self) -> None:
        self.controls = []
        self.closes = 0
        self.fail_close = False

    def summary(self) -> dict[str, Any]:
        # RuntimeDriver also supplies status/hold; registry stop state must win.
        return {"status": "running", "hold": None}

    def control(self, actor: Any, body: Any) -> dict[str, Any]:
        self.controls.append((actor, body))
        return {"action": "stop", "status": "stopped"}

    def task(self, body: Any) -> None:
        raise AssertionError("A stopping participant cannot accept more work")

    def close(self) -> None:
        self.closes += 1
        if self.fail_close:
            self.fail_close = False
            raise RuntimeError("runtime_shutdown_pending")


class ProgramStopFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.factories = []

    def request(self, operation: Any, **body: Any) -> dict[str, Any]:
        return {
            "profile": "band-contract/program-host-request/1",
            "room": "room",
            "session": "session",
            "actor": "User:owner",
            "operation": operation,
            "request": body,
            "context": {
                "source": {"sha256": "source"},
                "config": {"bindings": {"worker": "Agent:worker"}},
                "state": {"status": "active", "accepted_count": 0},
            },
        }

    def host(self, root: Any = None, factory: Any = None) -> Any:
        def default_factory(*args: Any) -> Any:
            participant = Participant()
            self.factories.append(participant)
            return participant

        host = ProgramHost(
            root or self.root,
            assessor=lambda *_: {"classification": "program", "deployable": True},
            factory=factory or default_factory,
            authors={"author"},
        )
        self.addCleanup(host.close)
        return host

    def start(self, host: Any, *, journal: bool = True) -> Any:
        host.operation(
            self.request("assess", id="assessment", role="worker", author_id="author")
        )
        host.step()
        start = self.request(
            "start",
            id="start",
            assessment_id="assessment",
            mode="program",
            journal=journal,
        )
        host.operation(start)
        host.step()
        return start

    def stop(self) -> Any:
        return self.request("control", id="stop", action="stop")
