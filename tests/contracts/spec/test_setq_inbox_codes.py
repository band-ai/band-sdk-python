"""Q-L3: a status-only cursor conflict must fail inbox conformance."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from contracts.spec.tools.checkinbox import (
    PATH,
    check,
    expected_response,
    receipts,
    validators,
)


class TestCursorAheadConformance(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = json.loads(PATH.read_text())

    def observed(self) -> list[dict[str, Any]]:
        expanded = receipts(self.bundle, validators())
        return [
            copy.deepcopy(expected_response(case, expanded))
            for case in self.bundle["cases"]
        ]

    def test_both_future_cursor_vectors_require_the_typed_code(self) -> None:
        conflicts = [
            case for case in self.bundle["cases"] if case["expected"]["status"] == 409
        ]
        self.assertEqual(
            {case["id"] for case in conflicts},
            {"after-head-refused", "empty-future-refused"},
        )
        for case in conflicts:
            self.assertEqual(
                case["expected"].get("body", {}).get("code"), "cursor_ahead"
            )

    def test_omitted_or_wrong_adapter_code_is_not_conformant(self) -> None:
        for code in (None, "room_conflict"):
            observed = self.observed()
            for actual in observed:
                if actual["status"] == 409:
                    actual["body"] = {"error": "authority-specific diagnostic"}
                    if code is not None:
                        actual["body"]["code"] = code
            with (
                self.subTest(code=code),
                self.assertRaisesRegex(ValueError, "cursor_ahead"),
            ):
                check(self.bundle, observed)

    def test_typed_adapter_code_allows_platform_specific_diagnostic(self) -> None:
        observed = self.observed()
        for actual in observed:
            if actual["status"] == 409:
                actual["body"] = {
                    "code": "cursor_ahead",
                    "error": "authority-specific diagnostic",
                }
        self.assertTrue(check(self.bundle, observed)["adapter_checked"])

    def test_vector_cannot_weaken_typed_conflict_to_status_only(self) -> None:
        case = next(
            case for case in self.bundle["cases"] if case["expected"]["status"] == 409
        )
        case["expected"] = {"status": 409}
        with self.assertRaises(ValueError):
            check(self.bundle)
