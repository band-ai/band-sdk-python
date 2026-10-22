"""N3Target specification regression checks."""

from __future__ import annotations

import copy
import unittest

from contracts.spec.tools import checkbandbinding as binding
from tests.contracts.spec.bandbinding.kit import fixture


class TestN3Target(unittest.TestCase):
    def test_reviewed_retention_targets(self) -> None:
        binding.check(fixture())

    def test_full_room_cannot_be_reclassified_as_transient_or_success(self) -> None:
        for expected in (
            {
                "http_status": 503,
                "body": {"error": "storage_unavailable"},
                "retryable": True,
                "unarchived_after": 256,
            },
            {"http_status": 201, "retryable": False, "unarchived_after": 257},
        ):
            target = fixture()
            next(
                case for case in target["retention"] if case["id"] == "full_new_create"
            )["expected"] = expected
            with (
                self.subTest(expected=expected),
                self.assertRaisesRegex(AssertionError, "retention decision"),
            ):
                binding.check(target)

    def test_unverified_or_active_archive_cannot_release_a_slot(self) -> None:
        for identifier in (
            "active_archive",
            "unverified_archive",
            "unresolved_archive",
            "unreachable_receipts_archive",
            "uncommitted_archive",
        ):
            target = fixture()
            next(case for case in target["retention"] if case["id"] == identifier)[
                "expected"
            ]["slot_released"] = True
            with (
                self.subTest(identifier=identifier),
                self.assertRaisesRegex(AssertionError, "retention decision"),
            ):
                binding.check(target)

    def test_archive_preserves_receipts_global_claim_and_exact_reconciliation(
        self,
    ) -> None:
        for field, value in [
            ("receipts_reachable", False),
            ("global_id_claim", False),
            ("exact_retry", None),
            ("cursor_after", 0),
        ]:
            target = fixture()
            next(
                case
                for case in target["retention"]
                if case["id"] == "verified_terminal_archive"
            )["expected"][field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(AssertionError, "retention decision"),
            ):
                binding.check(target)

    def test_exact_create_retry_at_capacity_does_not_allocate_a_second_slot(
        self,
    ) -> None:
        target = fixture()
        next(
            case
            for case in target["retention"]
            if case["id"] == "full_exact_create_retry"
        )["expected"] = copy.deepcopy(target["retention"][0]["expected"])
        with self.assertRaisesRegex(AssertionError, "retention decision"):
            binding.check(target)

    def test_retention_coverage_cannot_drop_archive_attacks(self) -> None:
        target = fixture()
        target["retention"] = target["retention"][:1]
        with self.assertRaisesRegex(AssertionError, "retention coverage"):
            binding.check(target)
