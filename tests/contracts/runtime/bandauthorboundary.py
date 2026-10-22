from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.band.authoring import Journal


class AuthorBoundaryFixture(unittest.TestCase):
    def fixture(self) -> tuple[Any, ...]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        journal = Journal(root)
        self.addCleanup(journal.close)
        return root, journal
