from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.program import Artifact
from tests.contracts.runtime.program import SOURCE


class ProvidersFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="hybrid-provider-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.artifact = Artifact.publish(SOURCE, self.path / "artifacts")
        self.bundle = {
            "program": self.artifact.to_dict(),
            "state": {"count": 0},
            "view": {},
            "input": {"id": "q", "payload": {"text": "ping"}},
        }

    def command(self, source: Any) -> list[Any]:
        path = self.path / (
            "command" + str(len(list(self.path.glob("command*")))) + ".py"
        )
        path.write_text(source)
        return [sys.executable, str(path)]
