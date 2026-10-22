from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from remote_agent.invocations import InvocationJournal
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from tests.contracts.runtime.setp15 import capture


class TransientProbeFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "agent.sqlite3"
        self.open_store()
        self.program = Program(
            Artifact.publish("# probe fixture", self.root / "artifacts")
        )
        self.request = {"op": "describe", "view": {}}
        self.timeout = {
            "exit_code": -9,
            "failure": "Callback timeout",
            "complete": False,
        }
        self.success = capture(
            {
                "schema": "1",
                "initial": {},
                "coverage": {"mode": "full", "handles": [], "delegates": []},
            }
        )

    def open_store(self) -> None:
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.journal = InvocationJournal(self.store)

    def run_probe(self, execution: Any) -> None:
        with patch.object(self.program, "execute", return_value=execution) as execute:
            self.assertEqual(self.journal.run(self.program, self.request), execution)
            execute.assert_called_once()
