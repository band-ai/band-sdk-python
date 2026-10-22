from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.invocations import InvocationJournal
from remote_agent.program import Artifact, Program
from remote_agent.store import Store


def capture(value: Any) -> dict[str, Any]:
    return {
        "exit_code": 0,
        "failure": None,
        "complete": True,
        "stdout": base64.b64encode(json.dumps(value).encode()).decode(),
        "stderr": "",
        "elapsed_ms": 1,
    }


class ProbeBoundsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.journal = InvocationJournal(self.store)
        self.program = Program(
            Artifact.publish("# probe fixture", self.root / "artifacts")
        )
        self.request = {"op": "describe", "view": {}}
        self.success = capture(
            {
                "schema": "1",
                "initial": {},
                "coverage": {"mode": "full", "handles": [], "delegates": []},
            }
        )
