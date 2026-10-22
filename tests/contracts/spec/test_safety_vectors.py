"""M22 explicit, portable compiler/roster boundaries must not become generic errors."""

from __future__ import annotations

import copy
import json
import unittest

from contracts.spec.tools.generatevectors import pointer
from tests.paths import CONTRACTS_ROOT as ROOT

NAMES = (
    "pattern-syntax",
    "pattern-cost",
    "entry-self",
    "entry-next",
    "entry-any-state",
    "entry-each",
    "entry-deadline",
    "entry-limit",
    "attributes-bytes",
)


class TestSafetyVector(unittest.TestCase):
    def test_all_named_safety_vectors_are_pinned_with_precise_errors(self) -> None:
        manifest = json.loads((ROOT / "spec/conformance/manifest.json").read_text())
        ids = {row["id"] for row in manifest["cases"]}
        self.assertTrue({"v2-m22-" + name for name in NAMES} <= ids)
        for name in NAMES:
            case = json.loads(
                (
                    ROOT / "spec/conformance" / ("case-v2-m22-" + name + ".json")
                ).read_text()
            )
            if name.startswith("entry-"):
                self.assertIn(
                    "initial_state_target", [a["equals"] for a in case["assertions"]]
                )
                self.assertTrue(
                    any(a["pointer"] == "/error/line" for a in case["assertions"])
                )
            # An implementation returning any unrelated compile error must fail the oracle.
            mutant = copy.deepcopy(case["expected"])
            for i, request in enumerate(case["requests"]):
                if request["op"] == "compile" and "error" in mutant[i]:
                    mutant[i] = {"error": {"code": "syntax", "line": 1, "detail": None}}
                if request["op"] == "new" and "error" in mutant[i]:
                    mutant[i] = {"error": "invalid_config"}
            self.assertTrue(
                any(
                    pointer(mutant[a["response"]], a["pointer"]) != a["equals"]
                    for a in case["assertions"]
                ),
                name,
            )


if __name__ == "__main__":
    unittest.main()
