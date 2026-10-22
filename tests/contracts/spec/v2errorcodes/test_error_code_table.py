"""ErrorCodeTable specification regression checks."""

from __future__ import annotations

import json
import unittest

from contracts.spec.tools import vectorsv2extras
from contracts.spec.tools.buildcontributionschemas import root as contributions
from contracts.spec.tools.buildv2schemas import root as v2
from tests.contracts.spec.v2errorcodes.kit import (
    GUIDE,
    SPEC,
    TIME,
    codes,
    enum,
    table_row,
    validator,
)


class TestErrorCodeTable(unittest.TestCase):
    def test_compiler_error_codes_equal_the_dsl_list(self) -> None:
        prose = (SPEC / "contract-dsl.md").read_text()
        sentence = prose.split("Required codes are ", 1)[1].split(". ", 1)[0]
        self.assertEqual(
            enum(v2, "compiler_error", "properties", "code"), codes(sentence)
        )

    def test_timer_target_is_a_plain_v2_compiler_error(self) -> None:
        error = {
            "code": "timer_target",
            "line": 13,
            "detail": "The state needs a declared clock",
        }
        self.assertTrue(validator(v2, "compiler_error").is_valid(error))

    def test_wire_restore_and_creation_errors_equal_their_rows(self) -> None:
        self.assertEqual(
            enum(v2, "wire_error", "properties", "error"),
            table_row("Wire decoding and carrier normalization"),
        )
        self.assertEqual(
            enum(v2, "restore_error", "properties", "error"),
            table_row("Checkpoint export and restore"),
        )
        self.assertEqual(
            enum(v2, "creation_error", "properties", "error"),
            table_row("Session creation (`new`)"),
        )
        self.assertIn(
            "completion_size", enum(v2, "creation_error", "properties", "error")
        )
        self.assertLessEqual(
            table_row("Extension negotiation at `new`"),
            enum(v2, "creation_error", "properties", "error"),
        )

    def test_source_loading_errors_equal_their_row(self) -> None:
        # Sofia's final review (M19): the bare-string loader codes that v2 vectors pin exactly.
        row = table_row("Source loading (`compile` of a file source)")
        self.assertEqual(enum(v2, "loader_error", "properties", "error"), row)
        self.assertLessEqual(
            {
                "digest_mismatch",
                "file_digest_mismatch",
                "frontmatter_required",
                "input",
            },
            row,
        )
        self.assertTrue(
            validator(v2, "loader_error").is_valid({"error": "file_digest_mismatch"})
        )
        self.assertFalse(
            validator(v2, "loader_error").is_valid({"error": "missing_field"})
        )

    def test_malformed_headers_take_the_compiler_shape(self) -> None:
        # Sofia set O LOW: header problems are compiler errors at the field's line, codes listed.
        self.assertLessEqual(
            {
                "header",
                "field_value",
                "unknown_field",
                "duplicate_field",
                "missing_field",
            },
            enum(v2, "compiler_error", "properties", "code"),
        )
        self.assertNotIn(
            "unsupported_encoding",
            table_row("Source loading (`compile` of a file source)"),
        )

    def test_a_non_current_engine_is_only_the_engine_compiler_error(self) -> None:
        # Sofia set O O4: one code per condition (B1); no bare file_profile_required.
        row_text = next(
            line for line in GUIDE.splitlines() if line.startswith("| Source loading")
        )
        self.assertNotIn(
            "file_profile_required", enum(v2, "loader_error", "properties", "error")
        )
        self.assertNotIn("file_profile_required", row_text)
        self.assertIn("compiler error `engine` at line 1", row_text)

    def test_vectors_never_compare_compile_error_detail(self) -> None:
        # contract-dsl.md: detail is diagnostic and not compared (Sofia set O LOW).
        pinned = [
            json.loads(path.read_text())
            for path in (SPEC / "conformance").glob("case-v2-*.json")
        ]
        for case in pinned + vectorsv2extras.cases():
            for assertion in case["assertions"]:
                with self.subTest(case=case["id"], assertion=assertion):
                    self.assertFalse(assertion["pointer"].startswith("/error/detail"))

    def test_restore_row_names_contract_mismatch(self) -> None:
        self.assertIn("contract_mismatch", table_row("Checkpoint export and restore"))
        self.assertIn(
            "contract_mismatch", enum(v2, "restore_error", "properties", "error")
        )

    def test_room_creation_errors_equal_their_row(self) -> None:
        # Omar set P P-9: room_new errors are enumerated like every other boundary.
        self.assertEqual(
            enum(v2, "room_creation_error", "properties", "error"),
            table_row("Room creation (`room_new`)"),
        )

    def test_time_errors_have_a_row(self) -> None:
        self.assertEqual(table_row("Any operation that takes `now`"), TIME)

    def test_contribution_rejections_equal_their_rows(self) -> None:
        self.assertIn("already_contributed", table_row("Contribution controls"))
        self.assertEqual(
            enum(contributions, "contribution_rejection", "properties", "reason"),
            table_row("Contribution controls") | table_row("Host evidence") | TIME,
        )

    def test_host_rejections_equal_their_row(self) -> None:
        # Host events follow host-signals.md: a bad or regressed time is stale_host_event (spec F1).
        statuses = {"duplicate_host_event", "control_budget_required"}
        reasons = enum(v2, "host_rejection", "properties", "reason")
        self.assertEqual(reasons, table_row("Host events") - statuses)
        self.assertEqual(reasons & TIME, set())

    def test_every_enumerated_code_is_named_in_prose(self) -> None:
        prose = set().union(*(codes(path.read_text()) for path in SPEC.glob("*.md")))
        names = [
            ("compiler_error", "code"),
            ("wire_error", "error"),
            ("restore_error", "error"),
            ("loader_error", "error"),
            ("creation_error", "error"),
            ("host_rejection", "reason"),
            ("room_creation_error", "error"),
        ]
        enumerated = set().union(
            *(enum(v2, name, "properties", key) for name, key in names)
        )
        enumerated |= enum(
            contributions, "contribution_rejection", "properties", "reason"
        )
        self.assertEqual(enumerated - prose, set())

    def test_decisions_refuse_an_unlisted_reason(self) -> None:
        host = validator(v2, "host_decision")
        contribution = validator(contributions, "contribution_decision")
        self.assertTrue(
            host.is_valid({"status": "rejected", "reason": "stale_host_event"})
        )
        self.assertFalse(host.is_valid({"status": "rejected", "reason": "stale"}))
        self.assertTrue(
            contribution.is_valid(
                {"status": "rejected", "reason": "already_contributed"}
            )
        )
        self.assertFalse(
            contribution.is_valid({"status": "rejected", "reason": "settled"})
        )
