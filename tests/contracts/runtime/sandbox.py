from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.program import Artifact, Program

PREAMBLE = """
from remote_agent.sdk import Handled
STATE_VERSION = "1"
COVERAGE = {"mode": "full", "handles": ["isolation probe"], "delegates": []}
def initial(view): return {}
"""


class SandboxFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="program-isolation-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def run_probe(self, body: Any) -> Any:
        source = (
            PREAMBLE
            + "\ndef handle(event, state, context):\n"
            + "\n".join("    " + line for line in body.splitlines())
            + "\n"
        )
        return Program(Artifact.publish(source, self.directory / "artifacts")).handle(
            {}, {}, {}
        )["state"]
