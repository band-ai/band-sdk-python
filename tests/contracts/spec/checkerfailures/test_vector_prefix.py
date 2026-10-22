"""VectorPrefix specification regression checks."""

from __future__ import annotations

import json
import tempfile
import unittest

import pytest

from contracts.spec.tools import generatevectors
from tests.contracts.spec.checkerfailures.kit import ROOT
from tests.contracts.spec.workspace import isolated_root, run_tool


class TestVectorPrefix(unittest.TestCase):
    """generatevectors.py crashed unless the prefix was a carrier prefix: a local `prefix` shadowed it."""

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_every_case_is_generated_without_a_prefix(self) -> None:
        ids = [case["id"] for case in generatevectors.cases()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIn("v2-checkpoint-restore", ids)
        self.assertNotIn("checkpoint-restore", ids)
        self.assertIn("wire-0", ids)

    def test_a_non_carrier_prefix_selects_exactly_the_pinned_cases(self) -> None:
        manifest = json.loads((ROOT / "spec/conformance/manifest.json").read_text())
        pinned = {
            entry["id"]
            for entry in manifest["cases"]
            if entry["id"].startswith("wire-")
        }
        self.assertEqual(
            {case["id"] for case in generatevectors.cases("wire-")}, pinned
        )

    @pytest.mark.contract_posix
    @pytest.mark.contract_engine
    def test_write_without_a_prefix_reproduces_the_pinned_fixtures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            result = run_tool(root, "generatevectors.py", "--write")
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            original = ROOT / "spec/conformance"
            generated = root / "spec/conformance"
            pinned = {
                path.relative_to(original)
                for path in original.rglob("*")
                if path.is_file()
            }
            written = {
                path.relative_to(generated)
                for path in generated.rglob("*")
                if path.is_file()
            }
            self.assertEqual(written, pinned)
            for name in sorted(pinned):
                with self.subTest(path=str(name)):
                    self.assertEqual(
                        (generated / name).read_bytes(), (original / name).read_bytes()
                    )
