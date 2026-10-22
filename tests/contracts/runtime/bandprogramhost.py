from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any

from remote_agent.band.host import ProgramHost
from remote_agent.store import Store


class ProgramHostFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.room = str(uuid.uuid4())
        self.session = str(uuid.uuid4())
        self.author = str(uuid.uuid4())
        self.context = {
            "source": {"sha256": "source"},
            "config": {"bindings": {"calculator": "Agent:c"}},
            "state": {"status": "active", "accepted_count": 0},
            "declarations": {},
            "history": [],
        }

    def request(self, operation: Any, **body: Any) -> dict[str, Any]:
        return {
            "profile": "band-contract/program-host-request/1",
            "room": self.room,
            "session": self.session,
            "actor": "User:owner",
            "operation": operation,
            "request": body,
            "context": self.context,
        }

    def host(self, **options: Any) -> Any:
        def assess(context: Any, request: Any) -> dict[str, Any]:
            self.calls.append(request)
            return {
                "classification": "program",
                "deployable": True,
                "reason": "Exact bounded arithmetic",
                "handles": ["typed arithmetic"],
                "delegates": [],
                "evidence": [],
                "source": "vetted source",
            }

        h = ProgramHost(
            options.pop("root", self.root),
            assessor=options.pop("assessor", assess),
            factory=options.pop("factory", lambda *args: None),
            authors={self.author},
            **options,
        )
        self.addCleanup(h.close)
        return h


class InterruptedAssessment(BaseException):
    pass


class DurableInstance:
    durable = True

    def summary(self) -> dict[str, Any]:
        return {}

    def close(self) -> None:
        pass


class EphemeralInstance(DurableInstance):
    durable = False

    def control(self, *args: Any) -> dict[str, Any]:
        return {"status": "completed"}


class RetryingTaskInstance(EphemeralInstance):
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    def task(self, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(body)
        if len(self.calls) == 1:
            raise ValueError("external_acknowledgement_unknown")
        return {"decision": {"status": "duplicate"}, "proposal_id": body["id"]}


class CheckpointInstance(DurableInstance):
    def __init__(self, store: Store) -> None:
        self.store = store

    def summary(self) -> dict[str, Any]:
        return {"checkpoint": self.store.get("checkpoint")}

    def control(self, *args: Any) -> dict[str, Any]:
        return {"status": "stopped"}

    def close(self) -> None:
        self.store.close()
