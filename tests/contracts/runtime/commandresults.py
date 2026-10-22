from __future__ import annotations

import sys
import unittest
from typing import Any


class CommandResultsFixture(unittest.TestCase):
    def command(self, text: Any) -> list[Any]:
        return [sys.executable, "-c", "import sys;sys.stdout.write(" + repr(text) + ")"]
