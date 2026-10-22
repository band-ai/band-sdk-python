"""CompilerDiagnostic specification regression checks."""

from __future__ import annotations

import copy
import unittest
from typing import Any, ClassVar

from contracts.spec.tools.checkspec import comparison_bytes


class TestCompilerDiagnostic(unittest.TestCase):
    request: ClassVar[dict[str, Any]] = {"op": "compile"}
    expected: ClassVar[dict[str, Any]] = {
        "error": {"code": "syntax", "line": 5, "detail": "+EXEC shell"}
    }

    def test_diagnostic_wording_and_structure_can_vary(self) -> None:
        for detail in (
            None,
            "Unsupported declaration",
            ["syntax", "+EXEC"],
            {"hint": "check line 5"},
        ):
            with self.subTest(detail=detail):
                actual = copy.deepcopy(self.expected)
                actual["error"]["detail"] = detail
                self.assertEqual(
                    comparison_bytes(self.request, actual),
                    comparison_bytes(self.request, self.expected),
                )

    def test_only_detail_is_ignored(self) -> None:
        wrong_code = copy.deepcopy(self.expected)
        wrong_code["error"]["code"] = "type"
        wrong_line = copy.deepcopy(self.expected)
        wrong_line["error"]["line"] = 6
        missing = copy.deepcopy(self.expected)
        del missing["error"]["detail"]
        extra = copy.deepcopy(self.expected)
        extra["error"]["extension"] = True
        outer = {**self.expected, "extension": True}
        for actual in (
            wrong_code,
            wrong_line,
            missing,
            extra,
            outer,
            {"error": "syntax"},
        ):
            with self.subTest(actual=actual):
                self.assertNotEqual(
                    comparison_bytes(self.request, actual),
                    comparison_bytes(self.request, self.expected),
                )

    def test_detail_cannot_bypass_the_protocol_json_domain(self) -> None:
        for detail in (1.0, float("nan"), 2**53, "\ud800"):
            actual = copy.deepcopy(self.expected)
            actual["error"]["detail"] = detail
            with (
                self.subTest(detail=repr(detail)),
                self.assertRaises((ValueError, UnicodeError)),
            ):
                comparison_bytes(self.request, actual)

    def test_success_and_other_operation_values_remain_exact(self) -> None:
        for request, first, second in (
            ({"op": "compile"}, {"ok": {"detail": "a"}}, {"ok": {"detail": "b"}}),
            (
                {"op": "query"},
                self.expected,
                {"error": {**self.expected["error"], "detail": "other"}},
            ),
            (
                {"op": "step"},
                {"decision": {"version": 1}},
                {"decision": {"version": 2}},
            ),
        ):
            with self.subTest(request=request):
                self.assertNotEqual(
                    comparison_bytes(request, first), comparison_bytes(request, second)
                )
