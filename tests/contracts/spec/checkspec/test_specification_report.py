"""SpecificationReport specification regression checks."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.spec.tools import checkspec


class TestSpecificationReport(unittest.TestCase):
    def test_report_distinguishes_examples_from_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            with (
                patch(
                    "sys.argv", ["checkspec", "--schema-only", "--output", str(path)]
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                checkspec.main()
            report = json.loads(path.read_text())
            self.assertIn("coverage", report)
            self.assertEqual(report["qualification"], "not_assessed")
            self.assertEqual(report["coverage"]["requirements"], report["requirements"])
            self.assertGreater(report["coverage"]["requirements_without_examples"], 0)
