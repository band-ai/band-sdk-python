from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remote_agent.band.authoring import Journal


class AgentServiceFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._journal: Journal | None = None

    @property
    def journal(self) -> Journal:
        if self._journal is None:
            self._journal = Journal(Path(self.tmp.name))
            self.addCleanup(self._journal.close)
        return self._journal
