"""OptimizedRun specification regression checks."""

from __future__ import annotations

import tempfile
import unittest

from tests.contracts.spec.workspace import isolated_root, run_tool


class TestOptimizedRun(unittest.TestCase):
    """The gates are assert statements; python -O stripped them and a corrupted bundle passed (spec M20)."""

    def test_a_corrupted_bundle_fails_with_and_without_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            case = root / "spec/conformance/case-wire-1.json"
            case.write_text(case.read_text().replace("duplicate_key", "json", 1))
            plain = run_tool(root, "checkspec.py", "--schema-only")
            self.assertNotEqual(plain.returncode, 0, plain.stdout[-300:])
            optimized = run_tool(root, "checkspec.py", "--schema-only", optimize=True)
            self.assertNotEqual(optimized.returncode, 0, optimized.stdout[-300:])
            self.assertNotIn('"passed": true', optimized.stdout)

    def test_every_gate_and_tool_refuses_to_run_optimized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            for tool in (
                "checkv2.py",
                "checkcoordination.py",
                "checkhttp.py",
                "checkcarrierexamples.py",
                "generatevectors.py",
                "h7sharednamespace.py",
                "v2unknownengine.py",
                "writerequirements.py",
                "lockpython.py",
            ):
                with self.subTest(tool=tool):
                    result = run_tool(root, tool, optimize=True)
                    self.assertNotEqual(result.returncode, 0, result.stdout[-300:])
                    self.assertIn("python -O", result.stderr)
