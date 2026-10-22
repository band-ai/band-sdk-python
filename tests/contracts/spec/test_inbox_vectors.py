"""Independent mutation pins for the inbox semantic checker, not room certification."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

from contracts.spec.tools.checkinbox import (
    PATH,
    adapter_requests,
    check,
    expected_response,
    page,
    receipts,
    validators,
)


class TestInboxVector(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle = json.loads(PATH.read_text())

    def case(self, identifier: str) -> dict[str, Any]:
        return next(row for row in self.bundle["cases"] if row["id"] == identifier)

    def rejects(self) -> None:
        with self.assertRaises((AssertionError, ValueError)):
            check(self.bundle)

    def test_independent_bundle_is_valid(self) -> None:
        report = check(self.bundle)
        self.assertGreaterEqual(report["cases"], 20)
        self.assertEqual(report["qualification"], "not_assessed")

    def test_hidden_receipt_is_not_a_visible_event(self) -> None:
        self.case("hidden-only-advances")["expected"]["body"]["events"] = ["hidden"]
        self.rejects()

    def test_hidden_receipt_counts_toward_scan_bound(self) -> None:
        self.case("scan-bound-includes-hidden")["expected"]["body"]["cursor"] = 99
        self.rejects()

    def test_later_overflow_cannot_advance_past_blocked_receipt(self) -> None:
        self.case("later-overflow-no-skip")["expected"]["body"]["cursor"] = 30
        self.rejects()

    def test_first_oversized_event_cannot_be_silently_skipped(self) -> None:
        self.case("first-visible-oversized")["expected"] = {
            "status": 200,
            "body": {"cursor": 10, "events": []},
        }
        self.rejects()

    def test_future_cursor_cannot_be_accepted(self) -> None:
        self.case("after-head-refused")["expected"] = {
            "status": 200,
            "body": {"cursor": 100, "events": []},
        }
        self.rejects()

    def test_future_cursor_is_conflict_not_bad_request(self) -> None:
        validate = validators()
        expanded = receipts(self.bundle, validate)
        for identifier in ("after-head-refused", "empty-future-refused"):
            with self.subTest(identifier=identifier):
                self.assertEqual(
                    page(self.case(identifier), expanded, validate),
                    {"status": 409, "body": {"code": "cursor_ahead"}},
                )

    def test_legacy_future_cursor_bad_request_is_rejected(self) -> None:
        self.case("after-head-refused")["expected"] = {"status": 400}
        self.rejects()

    def test_invalid_input_precedes_ahead_of_head_conflict(self) -> None:
        validate = validators()
        expanded = receipts(self.bundle, validate)
        for identifier in (
            "unsafe-after",
            "negative-after",
            "boolean-after",
            "zero-limit",
            "over-limit",
        ):
            with self.subTest(identifier=identifier):
                row = copy.deepcopy(self.case(identifier))
                if identifier in ("zero-limit", "over-limit"):
                    row["request"]["after"] = row["head"] + 1
                self.assertEqual(page(row, expanded, validate), {"status": 400})

    def test_retention_cannot_renumber_positions(self) -> None:
        self.case("compacted-stable-replay")["records"][1]["position"] = 41
        self.rejects()

    def test_retention_cannot_weaken_both_positions_and_expected_cursor(self) -> None:
        row = self.case("archived-stable-replay")
        row["records"][1]["position"] = 41
        row["expected"]["body"]["cursor"] = 41
        self.rejects()

    def test_original_decision_cannot_be_rewritten(self) -> None:
        self.bundle["receipts"]["small"]["decision"]["session"] = (
            "99999999-9999-4999-8999-999999999999"
        )
        self.rejects()

    def test_byte_bound_counts_utf8_not_code_points(self) -> None:
        self.bundle["receipts"]["unicode"]["canonical_bytes"] -= 1000
        self.rejects()

    def test_exact_page_bound_cannot_drop_last_fitting_event(self) -> None:
        self.case("exact-page-bound")["expected"]["body"]["events"] = ["large_a"]
        self.rejects()

    def test_invalid_boolean_cursor_cannot_be_success(self) -> None:
        self.case("boolean-after")["expected"] = {
            "status": 200,
            "body": {"cursor": 99, "events": ["small"]},
        }
        self.rejects()

    def test_error_response_has_no_acknowledged_cursor(self) -> None:
        self.case("blocked-next-page-refused")["expected"]["body"] = {
            "cursor": 30,
            "events": [],
        }
        self.rejects()

    def test_adapter_inputs_never_include_expected_results(self) -> None:
        requests = list(adapter_requests(self.bundle))
        self.assertEqual(len(requests), len(self.bundle["cases"]))
        self.assertTrue(all("expected" not in request for request in requests))

    def observed(self) -> list[dict[str, Any]]:
        expanded = receipts(self.bundle, validators())
        responses = []
        for case in self.bundle["cases"]:
            wanted = copy.deepcopy(expected_response(case, expanded))
            if wanted["status"] != 200:
                # Adapter shape: a bounded error body; the 409 also carries the shared code.
                wanted = {
                    "status": wanted["status"],
                    "body": {"error": "refused", **wanted.get("body", {})},
                }
            responses.append(wanted)
        return responses

    def test_matching_adapter_output_does_not_claim_live_qualification(self) -> None:
        report = check(self.bundle, self.observed())
        self.assertTrue(report["adapter_checked"])
        self.assertFalse(report["band_live_checked"])
        self.assertEqual(report["qualification"], "not_assessed")

    def test_adapter_response_count_is_exact(self) -> None:
        with self.assertRaisesRegex(ValueError, "response count"):
            check(self.bundle, self.observed()[:-1])

    def test_adapter_cannot_return_cursor_on_503(self) -> None:
        observed = self.observed()
        index = next(
            i
            for i, row in enumerate(self.bundle["cases"])
            if row["id"] == "blocked-next-page-refused"
        )
        observed[index]["body"] = {"cursor": 30, "events": []}
        with self.assertRaisesRegex(ValueError, "acknowledges input"):
            check(self.bundle, observed)

    def test_adapter_ahead_of_head_409_requires_cursor_ahead_code(self) -> None:
        observed = self.observed()
        index = next(
            i
            for i, row in enumerate(self.bundle["cases"])
            if row["id"] == "after-head-refused"
        )
        observed[index] = {
            "status": 409,
            "body": {"error": "Inbox cursor is ahead of the room journal"},
        }
        with self.assertRaisesRegex(ValueError, "cursor_ahead"):
            check(self.bundle, observed)

    def test_adapter_ahead_of_head_409_requires_a_string_error(self) -> None:
        index = next(
            i
            for i, row in enumerate(self.bundle["cases"])
            if row["id"] == "after-head-refused"
        )
        for body in (
            {"code": "cursor_ahead"},
            {"code": "cursor_ahead", "error": None},
            {"code": "cursor_ahead", "error": 409},
        ):
            observed = self.observed()
            observed[index] = {"status": 409, "body": body}
            with (
                self.subTest(body=body),
                self.assertRaisesRegex(ValueError, "lacks {error"),
            ):
                check(self.bundle, observed)

    def test_adapter_cannot_leak_a_hidden_receipt(self) -> None:
        observed = self.observed()
        index = next(
            i
            for i, row in enumerate(self.bundle["cases"])
            if row["id"] == "hidden-only-advances"
        )
        observed[index]["body"]["events"] = [
            self.bundle["receipts"]["hidden"]["decision"]
        ]
        with self.assertRaisesRegex(ValueError, "response differs"):
            check(self.bundle, observed)


if __name__ == "__main__":
    unittest.main()
