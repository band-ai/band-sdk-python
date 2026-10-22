from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from remote_agent.storage import canonical


class FileSourceTransportFixture(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "source.json"

    def write(self, value: Any) -> Any:
        self.path.write_bytes(canonical(value))
        self.path.chmod(0o600)
        return self.path
