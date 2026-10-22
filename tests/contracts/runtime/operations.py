from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.invocations import InvocationJournal
from remote_agent.program import Artifact, Program
from remote_agent.store import Store
from tests.contracts.runtime.program import SOURCE


class OperationsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="contract-ops-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.artifact = Artifact.publish(SOURCE, self.root / "artifacts")
        self.program = Program(self.artifact, journal=InvocationJournal(self.store))

    def cli(self, *args: Any, code: int = 0) -> Any:
        result = subprocess.run(
            [sys.executable, "-m", "remote_agent", *map(str, args)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, code, result.stderr)
        return json.loads(result.stdout) if result.stdout else None


class InterruptedReplay(BaseException):
    pass
