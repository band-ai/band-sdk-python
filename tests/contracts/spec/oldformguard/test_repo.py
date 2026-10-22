"""Repo specification regression checks."""

from __future__ import annotations

import unittest
from collections import Counter

from contracts.spec.tools import oldformguard as guard


class TestRepo(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.hits = [h for h in guard.scan() if h["verdict"] != "exempt"]
        cls.rows = guard.load_inventory()["rows"]

    def test_every_old_form_is_inventoried_and_every_row_still_hits(self) -> None:
        live = Counter(guard.inventory_key(h) for h in self.hits)
        listed = Counter(guard.inventory_key(r) for r in self.rows)
        new = [h for h in self.hits if (live - listed)[guard.inventory_key(h)]]
        stale = [r for r in self.rows if (listed - live)[guard.inventory_key(r)]]
        self.assertEqual(
            new, [], "old send-then-answer form: write a reply head (RULE.md section 1)"
        )
        self.assertEqual(stale, [], "converted: run oldformguard.py --shrink")

    def test_inline_sources_hold_no_old_form_rows(self) -> None:
        # T1 timed heads (RULE.md A.2) converted the last inline rows; the group rows wait for section 3.
        left = {
            (r["path"], r["state"], r["request"])
            for r in self.rows
            if r["owner"] == "Priya" and r["verdict"] == "error"
        }
        self.assertEqual(left, set())

    def test_the_compilers_own_negative_examples_are_not_inventoried(self) -> None:
        # Kenji's reply_required tests write the old form on purpose; they are the rule's negatives.
        self.assertTrue(
            guard.is_record("contracts/contract-lab/test/reply_required_test.exs")
        )
        self.assertEqual([r for r in self.rows if guard.is_record(r["path"])], [])

    def test_rows_carry_their_coverage_owner(self) -> None:
        for row in self.rows:
            self.assertEqual(row["owner"], guard.owner(row["path"]), row)
