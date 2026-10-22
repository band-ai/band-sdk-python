"""Prove the portable unknown-engine oracle rejects fail-open and late-refusal regressions."""

from __future__ import annotations

import copy
import json
import unittest

from contracts.spec.tools import v2unknownengine as unknown
from contracts.spec.tools.generatevectors import pointer


class TestUnknownEngineOracle(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(unknown.PATH.read_text())

    def test_valid_portable_fixture(self) -> None:
        unknown.check(self.fixture, self.fixture["expected"])

    def test_an_unknown_engine_that_compiles_is_rejected(self) -> None:
        mutant = copy.deepcopy(self.fixture)
        mutant["expected"][0] = {"ok": {"engine": "9.9.9"}}
        with self.assertRaisesRegex(AssertionError, "an unknown engine compiled"):
            unknown.check(mutant, mutant["expected"])

    def test_a_later_field_error_is_rejected_even_with_rewritten_expectation(
        self,
    ) -> None:
        mutant = copy.deepcopy(self.fixture)
        mutant["expected"][0] = {"error": ["missing_field", "lifetime"]}
        mutant["assertions"][0] = {
            "response": 0,
            "pointer": "/error/0",
            "equals": "missing_field",
        }
        with self.assertRaisesRegex(AssertionError, "must fail with code engine"):
            unknown.check(mutant, mutant["expected"])

    def test_fixture_assertions_hold_against_its_expected_responses(self) -> None:
        # Sofia F2: the native request's assertions must agree with its frontmatter_required response.
        native = [
            i
            for i, r in enumerate(self.fixture["requests"])
            if r["source"]["format"] == "native"
        ]
        self.assertEqual(
            [a for a in self.fixture["assertions"] if a["response"] in native],
            [
                {"response": i, "pointer": "/error", "equals": "frontmatter_required"}
                for i in native
            ],
        )
        for a in self.fixture["assertions"]:
            with self.subTest(assertion=a):
                self.assertEqual(
                    pointer(self.fixture["expected"][a["response"]], a["pointer"]),
                    a["equals"],
                )

    def test_check_evaluates_the_fixture_assertions(self) -> None:
        mutant = copy.deepcopy(self.fixture)
        mutant["assertions"][0] = {"response": 0, "pointer": "/error/line", "equals": 2}
        with self.assertRaisesRegex(AssertionError, "assertion"):
            unknown.check(mutant, mutant["expected"])

    def test_fixture_declares_its_schema_invalid_native_request(self) -> None:
        # Sofia set P P-L5: conformance/README.md requires each intentional violation declared.
        self.assertEqual(
            self.fixture.get("source_schema_assertions"),
            [{"request": 5, "valid": False}],
        )

    def test_check_enforces_the_schema_declaration(self) -> None:
        missing = copy.deepcopy(self.fixture)
        missing.pop("source_schema_assertions", None)
        with self.assertRaisesRegex(AssertionError, "source_schema_assertions"):
            unknown.check(missing, missing["expected"])
        wrong = copy.deepcopy(self.fixture)
        wrong["source_schema_assertions"] = [{"request": 5, "valid": True}]
        with self.assertRaisesRegex(AssertionError, "source_schema_assertions"):
            unknown.check(wrong, wrong["expected"])

    def test_requests_cannot_be_swapped_for_a_supported_engine(self) -> None:
        mutant = copy.deepcopy(self.fixture)
        mutant["requests"][0]["source"]["content"] = unknown.frontmatter("0.2.0-lab.1")
        with self.assertRaisesRegex(AssertionError, "requests changed"):
            unknown.check(mutant, mutant["expected"])


if __name__ == "__main__":
    unittest.main()
