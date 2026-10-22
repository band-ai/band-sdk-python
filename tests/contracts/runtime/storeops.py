from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.store import Store


class StoreOperationsFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="store-operations-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "agent.sqlite3"

    def open(self) -> Any:
        store = Store(self.path)
        self.addCleanup(store.close)
        return store
