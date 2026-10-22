from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from remote_agent.transport import EngineProcess
from tests.contracts.runtime.durableroom.kit import REPLIES


class ReferenceReplyRoomFixture(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="reference-replies-")
        self.addCleanup(temp.cleanup)
        self.contract = Path(temp.name) / "deploy.band"
        self.contract.write_text(REPLIES)
        self.engine = EngineProcess(self.contract)
        self.addCleanup(self.engine.close)
