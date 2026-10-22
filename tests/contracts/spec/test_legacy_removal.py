"""X4 wave2 must remove old compilation without losing approved semantic obligations."""

from __future__ import annotations

import hashlib
import json
import unittest

from contracts.spec.tools import generatevectors
from tests.paths import CONTRACTS_ROOT as ROOT

BUNDLE = ROOT / "spec/conformance"


class TestLegacyRemoval(unittest.TestCase):
    def test_no_manifest_request_uses_a_legacy_source(self) -> None:
        manifest = json.loads((BUNDLE / "manifest.json").read_text())
        for row in manifest["cases"]:
            fixture = json.loads((BUNDLE / row["path"]).read_text())
            for request in fixture["requests"]:
                self.assertNotIn("card", request.get("source", {}), row["id"])

    def test_all_old_source_files_are_retired_but_portable_cases_are_exact(
        self,
    ) -> None:
        baseline = json.loads((BUNDLE / "baseline-v1-pins.json").read_text())
        legacy = {case["id"] for case in baseline["cases"] if case["legacy_source"]}
        manifest = json.loads((BUNDLE / "manifest.json").read_text())
        self.assertEqual(len(legacy), 62)
        for row in baseline["manifest"]["cases"]:
            if row["id"] in legacy:
                self.assertFalse((BUNDLE / row["path"]).exists(), row["id"])
            else:
                self.assertIn(row, manifest["cases"])
                self.assertEqual(
                    hashlib.sha256((BUNDLE / row["path"]).read_bytes()).hexdigest(),
                    row["sha256"],
                )
        self.assertEqual(
            hashlib.sha256(
                (BUNDLE / "assertion-parity-v2.json").read_bytes()
            ).hexdigest(),
            "0106c2421c74aa5419499e6cb528164786ad31a788ed575160b4a137ef78d8c9",
        )
        self.assertEqual(generatevectors.check_parity()["unique_targets"], 229)


if __name__ == "__main__":
    unittest.main()
