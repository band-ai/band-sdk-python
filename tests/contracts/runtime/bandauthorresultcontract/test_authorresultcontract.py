from __future__ import annotations

import unittest

from jsonschema import Draft202012Validator

from remote_agent.band.authoring import output_schema, validate_result
from tests.contracts.runtime.bandauthorresultcontract.kit import (
    JOB,
    invalid_outputs,
    output,
)


class TestAuthorResultContract(unittest.TestCase):
    def test_runtime_rejects_every_canonical_result_boundary_violation(self) -> None:
        for name, value in invalid_outputs().items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_result(JOB, value)

    def test_schema_enforces_all_expressible_bounds_without_rejecting_valid_unicode(
        self,
    ) -> None:
        validator = Draft202012Validator(output_schema(JOB))
        for name, value in invalid_outputs().items():
            if name.endswith("_utf8"):
                # JSON Schema counts code points; the runtime supplies byte limits.
                continue
            with self.subTest(name=name):
                self.assertFalse(validator.is_valid(value), name)
        for unit, source_count, item_count in (
            ("x", 400000, 4000),
            ("é", 200000, 2000),
            ("😀", 100000, 1000),
        ):
            with self.subTest(unit=unit):
                value = output(
                    source=unit * source_count,
                    unresolved=[unit * item_count] * 16,
                    derivation=[{"evidence": "m1", "rule": unit * item_count}] * 256,
                )
                validate_result(JOB, value)
                self.assertTrue(validator.is_valid(value))
        validate_result(JOB, output(source="x", unresolved=[], derivation=[]))
        no_history = {"operation": "generate", "history": []}
        validator = Draft202012Validator(output_schema(no_history))
        self.assertFalse(validator.is_valid(output()))
        self.assertTrue(validator.is_valid(output(derivation=[])))
        with self.assertRaises(ValueError):
            validate_result(no_history, output())
