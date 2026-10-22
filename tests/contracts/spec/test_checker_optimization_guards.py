"""Each assertion-based checker must reject -O before delegating or checking data."""

from __future__ import annotations

import unittest

from tests.contracts.spec.workspace import run_tool
from tests.paths import CONTRACTS_ROOT


class TestCheckerOptimizationGuard(unittest.TestCase):
    def test_each_checker_rejects_optimization_before_loading(self) -> None:
        for name in (
            "checkbandbinding.py",
            "vectorsv2extras.py",
            "uncheckedmarkers.py",
        ):
            with self.subTest(name=name):
                result = run_tool(CONTRACTS_ROOT, name, optimize=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("refuse to run under python -O", result.stderr)


if __name__ == "__main__":
    unittest.main()
