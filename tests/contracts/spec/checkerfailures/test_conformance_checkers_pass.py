"""ConformanceCheckersPass specification regression checks."""

from __future__ import annotations

import tempfile
import unittest

import pytest

from tests.contracts.spec.checkerfailures.kit import ROOT
from tests.contracts.spec.workspace import isolated_root, run_tool


class TestConformanceCheckersPass(unittest.TestCase):
    """checkv2 and checkcoordination compiled old send-then-answer sources that reply_required
    refuses, and no test ran them end to end (op-kgf6fc3z)."""

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_check_v2_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            result = run_tool(root, "checkv2.py")
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('"passed": true', result.stdout)

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_check_coordination_passes_and_reproduces_its_pinned_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            result = run_tool(root, "checkcoordination.py")
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            report = "spec/conformance/coordination-report.json"
            self.assertEqual((root / report).read_bytes(), (ROOT / report).read_bytes())
