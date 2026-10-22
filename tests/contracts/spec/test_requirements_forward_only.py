"""The requirements catalog and its coverage index name only current profiles and examples (spec K3, L1)."""

from __future__ import annotations

import json
import unittest

from contracts.spec.tools import buildcoverage
from tests.paths import CONTRACT_SPEC_ROOT as SPEC


class TestForwardOnlyCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = json.loads((SPEC / "requirements.json").read_text())
        self.rows = {row["id"]: row for row in self.catalog["requirements"]}
        self.coverage = {
            row["id"]: row
            for row in json.loads((SPEC / "conformance/coverage.json").read_text())[
                "requirements"
            ]
        }

    def test_no_row_keeps_a_retired_profile(self) -> None:
        retired = {"compiler/1", "session-engine/1"}
        self.assertEqual(
            [
                row["id"]
                for row in self.catalog["requirements"]
                if retired & set(row["profiles"])
            ],
            [],
        )

    def test_historical_band_examples_are_not_cited(self) -> None:
        for identifier in ("BAND-001", "NET-003"):
            with self.subTest(identifier=identifier):
                self.assertEqual(
                    self.coverage[identifier]["examples"]["artifacts"],
                    ["band-wire-examples-v2.json"],
                )
        cited = {
            path
            for row in self.coverage.values()
            for path in row["examples"]["artifacts"]
        }
        self.assertEqual(cited & buildcoverage.HISTORICAL_ARTIFACTS, set())

    def test_band_003_requires_the_proposal_id_canary(self) -> None:
        self.assertIn(
            "private canary in the proposal ID", self.rows["BAND-003"]["acceptance"][0]
        )
        self.assertIn("receipt_handle", self.rows["BAND-003"]["acceptance"][0])


if __name__ == "__main__":
    unittest.main()
