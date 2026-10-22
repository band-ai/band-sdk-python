"""The v2 checks carry requirement IDs and feed a v2 coverage index (spec M24)."""

from __future__ import annotations

import json
import unittest
from typing import Any

from contracts.spec.tools import buildcoverage, v2trace
from tests.paths import CONTRACT_SPEC_ROOT as SPEC


class TestV2Coverage(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((SPEC / "requirements-v2.json").read_text())

    @property
    def index(self) -> dict[str, Any]:
        return json.loads((SPEC / "conformance/coverage-v2.json").read_text())

    def test_index_is_current(self) -> None:
        self.assertEqual(
            self.index,
            buildcoverage.coverage_v2(self.catalog, v2trace.TAGS),
            "Regenerate with buildcoverage.py",
        )

    def test_index_lists_every_v2_row_and_obligation(self) -> None:
        self.assertEqual(
            [row["id"] for row in self.index["requirements"]],
            [row["id"] for row in self.catalog["requirements"]],
        )
        for row, source in zip(
            self.index["requirements"], self.catalog["requirements"]
        ):
            self.assertEqual(
                [item["obligation"] for item in row["acceptance"]], source["acceptance"]
            )
        self.assertEqual(self.index["qualification"], "not_assessed")

    def test_unasserted_obligations_are_listed(self) -> None:
        missing = [
            f"{row['id']}/{n}"
            for row in self.index["requirements"]
            for n, item in enumerate(row["acceptance"], 1)
            if not item["checks"]
        ]
        self.assertEqual(self.index["unasserted"], missing)
        self.assertEqual(
            self.index["summary"]["acceptance_items_without_checks"], len(missing)
        )

    def test_every_tag_names_a_catalog_item(self) -> None:
        for tool, tags in v2trace.TAGS.items():
            with self.subTest(tool=tool):
                v2trace.verify(tool, list(tags), self.catalog)
        with self.assertRaises(AssertionError):
            v2trace.verify(
                "checkv2",
                list(v2trace.TAGS["checkv2"]) + ["untagged-check"],
                self.catalog,
            )

    def test_no_check_is_left_untagged(self) -> None:
        # Set N follow-up (Ada): no bare (). A check names v2 requirement rows, or says it is
        # tooling-only, or names the spec file whose rule has no v2 row yet.
        bare = [
            f"{tool}:{name}"
            for tool, table in v2trace.TAGS.items()
            for name, tags in table.items()
            if not tags
        ]
        self.assertEqual(bare, [])

    def test_markers_are_explicit_and_stand_alone(self) -> None:
        known = v2trace.rows(self.catalog)
        for tags in [
            (v2trace.TOOLING,),
            (v2trace.NO_ROW + "engine.md",),
            ("V2-ID/1", "V2-FAIL-CLOSED"),
        ]:
            v2trace.check_tags("ok", tags, known)
        for tags in [
            (),
            (v2trace.TOOLING, "V2-ID"),
            (v2trace.NO_ROW + "engine.md", "V2-ID"),
            (v2trace.NO_ROW + "missing.md",),
            ("V2-NOPE",),
            ("V2-ID/3",),
        ]:
            with self.subTest(tags=tags), self.assertRaises(AssertionError):
                v2trace.check_tags("bad", tags, known)

    def test_index_separates_tooling_from_rules_without_a_row(self) -> None:
        index = self.index
        self.assertNotIn("untraced_checks", index)
        tooling = [
            f"{tool}:{name}"
            for tool, table in v2trace.TAGS.items()
            for name, tags in table.items()
            if tags == (v2trace.TOOLING,)
        ]
        self.assertEqual(index["tooling_only_checks"], tooling)
        without_row = {}
        for tool, table in v2trace.TAGS.items():
            for name, tags in table.items():
                if tags[0].startswith(v2trace.NO_ROW):
                    without_row.setdefault(tags[0][len(v2trace.NO_ROW) :], []).append(
                        f"{tool}:{name}"
                    )
        self.assertEqual(
            index["checks_without_v2_row"], dict(sorted(without_row.items()))
        )

    def test_every_v2_manifest_case_is_tagged(self) -> None:
        # Sofia's final review (M24): the index also reads the pinned case-v2-* vectors.
        manifest = json.loads((SPEC / "conformance/manifest.json").read_text())
        v2_cases = [
            case["id"] for case in manifest["cases"] if case["id"].startswith("v2-")
        ]
        self.assertEqual(sorted(v2trace.TAGS["manifest"]), sorted(v2_cases))
        known = v2trace.rows(self.catalog)
        for name, tags in v2trace.TAGS["manifest"].items():
            v2trace.check_tags(name, tags, known)

    def test_index_counts_manifest_cases(self) -> None:
        items = {
            f"{row['id']}/{n}": item["checks"]
            for row in self.index["requirements"]
            for n, item in enumerate(row["acceptance"], 1)
        }
        self.assertIn("manifest:v2-reject-due-all", items["V2-DUE/1"])
        self.assertIn("manifest:v2-utf8-and-grapheme-boundaries", items["V2-TEXT/2"])
        self.assertNotIn("V2-DUE/1", self.index["unasserted"])
        self.assertNotIn("V2-TEXT/2", self.index["unasserted"])

    def test_coordination_report_carries_requirement_ids(self) -> None:
        report = json.loads((SPEC / "conformance/coordination-report.json").read_text())
        self.assertEqual(
            report["requirements"],
            {
                name: list(v2trace.TAGS["checkcoordination"][name])
                for name in report["checks"]
            },
        )

    def test_readme_names_both_indexes_without_overclaiming(self) -> None:
        readme = (SPEC / "conformance/README.md").read_text()
        self.assertNotIn("lists **every** requirement, its", readme)
        self.assertIn("(coverage-v2.json)", readme)
        self.assertIn("`unasserted`", readme)


if __name__ == "__main__":
    unittest.main()
