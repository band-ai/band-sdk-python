from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remote_agent.program import Artifact


class TimeoutPrecisionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="timeout-precision-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self._artifact: Artifact | None = None

    @property
    def artifact(self) -> Artifact:
        if self._artifact is None:
            self._artifact = Artifact.publish(
                "# no execution needed\n", self.root / "artifacts"
            )
        return self._artifact
