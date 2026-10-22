from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.store import Store


class StoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix="remote-agent-store-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "agent.sqlite3"

    def open(self, **options: Any) -> Any:
        store = Store(self.path, **options)
        self.addCleanup(store.close)
        return store
