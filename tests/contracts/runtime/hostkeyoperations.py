from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remote_agent.hostkey import HostKey
from remote_agent.store import Store


class HostKeyOperationsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="h7-key-ops-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "state.sqlite3")
        self.addCleanup(self.store.close)
        self.host = HostKey(self.store)
        self.host.ensure()
