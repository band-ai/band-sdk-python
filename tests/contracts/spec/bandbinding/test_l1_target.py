"""L1Target specification regression checks."""

from __future__ import annotations

import copy
import unittest

from contracts.spec.tools import checkbandbinding as binding
from tests.contracts.spec.bandbinding.kit import fixture


class TestL1Target(unittest.TestCase):
    def test_reviewed_visibility_targets(self) -> None:
        binding.check(fixture())

    def test_raw_id_payload_and_documentation_cannot_enter_shared_records(self) -> None:
        for surface in ("shared_history", "notifications"):
            for value in ("proposal_id", "payload", "documentation"):
                target = fixture()
                case = target["visibility"][0]
                case["expected"][surface][0]["contract"][value] = copy.deepcopy(
                    case["context"][value]
                )
                with (
                    self.subTest(surface=surface, value=value),
                    self.assertRaisesRegex(
                        AssertionError, "visibility (decision|canary)"
                    ),
                ):
                    binding.check(target)

    def test_handle_must_use_session_pipe_exact_utf8_id_not_id_alone(self) -> None:
        for incorrect in ("0" * 64, "A" * 64, "private-canary-id", "a" * 63):
            target = fixture()
            case = target["visibility"][0]
            for surface in ("shared_history", "notifications"):
                case["expected"][surface][0]["contract"]["receipt_handle"] = incorrect
            case["expected"]["stable_retry_handle"] = incorrect
            with (
                self.subTest(incorrect=incorrect),
                self.assertRaisesRegex(AssertionError, "visibility decision"),
            ):
                binding.check(target)

    def test_private_reconciliation_keeps_exact_original_id(self) -> None:
        target = fixture()
        target["visibility"][0]["expected"]["private_link"]["proposal_id"] = "redacted"
        with self.assertRaisesRegex(AssertionError, "visibility decision"):
            binding.check(target)

    def test_canary_probe_cannot_be_removed_or_changed_into_a_benign_id(self) -> None:
        for mutation in ("remove", "replace"):
            target = fixture()
            if mutation == "remove":
                target["visibility"] = []
            else:
                target["visibility"][0]["context"]["proposal_id"] = "safe-id"
            with (
                self.subTest(mutation=mutation),
                self.assertRaisesRegex(AssertionError, "visibility (coverage|inputs)"),
            ):
                binding.check(target)
