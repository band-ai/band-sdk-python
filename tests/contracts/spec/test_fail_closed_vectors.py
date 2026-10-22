"""M21 portable coverage must include all seven independent negative categories."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest

from contracts.spec.tools import generatevectors
from contracts.spec.tools.negativevectorsm21 import cases
from tests.contracts.spec.workspace import isolated_root, run_tool
from tests.paths import CONTRACTS_ROOT as ROOT

CATEGORIES = (
    "unknown-engine",
    "file-integrity",
    "source-pin",
    "state-profile",
    "checkpoint-profile",
    "view-profile",
    "cross-profile-state",
)


class TestFailClosedVector(unittest.TestCase):
    def test_seven_named_negatives_are_manifest_pinned(self) -> None:
        manifest = json.loads((ROOT / "spec/conformance/manifest.json").read_text())
        ids = {row["id"] for row in manifest["cases"]}
        self.assertTrue({"v2-m21-" + name for name in CATEGORIES} <= ids)

    def test_recomputed_checksum_mutations_and_bad_envelopes_are_not_echo_only(
        self,
    ) -> None:
        for case in cases():
            fixture = json.loads(
                (
                    ROOT / "spec/conformance" / ("case-" + case["id"] + ".json")
                ).read_text()
            )
            expected = copy.deepcopy(fixture["expected"])
            # A checksum-only restorer / permissive loader accepts every invalid input.
            for i, request in enumerate(case["requests"]):
                if request["op"] == "restore":
                    expected[i] = {"ok": copy.deepcopy(request["checkpoint"])}
                elif request["op"] == "check":
                    expected[i] = {"status": "accepted"}
                elif "error" in expected[i]:
                    expected[i] = {"ok": copy.deepcopy(request.get("source"))}
            failures = 0
            for assertion in case["assertions"]:
                try:
                    matched = (
                        generatevectors.pointer(
                            expected[assertion["response"]], assertion["pointer"]
                        )
                        == assertion["equals"]
                    )
                except (KeyError, TypeError, IndexError):
                    matched = False
                failures += not matched
            self.assertGreater(failures, 0, case["id"])

    def test_schema_negative_annotation_is_itself_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = isolated_root(directory)
            path = root / "spec/conformance/case-v2-m21-file-integrity.json"
            fixture = json.loads(path.read_text())
            fixture["source_schema_assertions"][0]["valid"] = True
            path.write_text(json.dumps(fixture, indent=2) + "\n")
            manifest_path = root / "spec/conformance/manifest.json"
            manifest = json.loads(manifest_path.read_text())
            next(row for row in manifest["cases"] if row["id"] == fixture["id"])[
                "sha256"
            ] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
            result = run_tool(root, "checkspec.py", "--schema-only")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Source schema validity differs", result.stderr)


if __name__ == "__main__":
    unittest.main()
