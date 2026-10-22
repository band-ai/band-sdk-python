"""CoordinationRace specification regression checks."""

from __future__ import annotations

import json
import unittest

from tests.contracts.spec.checkerfailures.kit import RACE_CHECKS, ROOT


class TestCoordinationRace(unittest.TestCase):
    """checkcoordination had no assertion for the contribution and host-signal conflict and race MUSTs (spec M23)."""

    def test_the_committed_report_asserts_every_race(self) -> None:
        report = json.loads(
            (ROOT / "spec/conformance/coordination-report.json").read_text()
        )
        self.assertTrue(report["passed"])
        self.assertEqual(sorted(RACE_CHECKS - set(report["checks"])), [])
