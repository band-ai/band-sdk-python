"""N1Target specification regression checks."""

from __future__ import annotations

import copy
import unittest

from contracts.spec.tools import checkbandbinding as binding
from tests.contracts.spec.bandbinding.kit import fixture


class TestN1Target(unittest.TestCase):
    def test_reviewed_creation_targets(self) -> None:
        binding.check(fixture())

    def test_participant_cannot_become_creator_by_rewriting_expected_identity(
        self,
    ) -> None:
        target = fixture()
        denied = next(
            case for case in target["creation"] if case["id"] == "participant_create"
        )
        denied["expected"] = copy.deepcopy(target["creation"][1]["expected"])
        with self.assertRaisesRegex(AssertionError, "creation decision"):
            binding.check(target)

    def test_human_create_cannot_grant_identity_or_private_data(self) -> None:
        for operation in ("identity", "view", "proposals", "inbox"):
            target = fixture()
            denied = next(
                case
                for case in target["creation"]
                if case["id"] == "human_" + operation
            )
            denied["expected"] = {"http_status": 200}
            with (
                self.subTest(operation=operation),
                self.assertRaisesRegex(AssertionError, "creation decision"),
            ):
                binding.check(target)

    def test_authorization_attack_cannot_be_removed_or_relabelled(self) -> None:
        for mutation in ("remove", "relabel"):
            target = fixture()
            if mutation == "remove":
                target["creation"] = [
                    case
                    for case in target["creation"]
                    if case["id"] != "participant_create"
                ]
            else:
                next(
                    case
                    for case in target["creation"]
                    if case["id"] == "participant_create"
                )["context"]["permission"] = "admin"
            with (
                self.subTest(mutation=mutation),
                self.assertRaisesRegex(AssertionError, "creation (coverage|inputs)"),
            ):
                binding.check(target)
