"""TypedError specification regression checks."""

from __future__ import annotations

import inspect
import unittest

import jsonschema

from contracts.spec.tools import checkv2
from contracts.spec.tools.buildv2schemas import root as v2
from tests.contracts.spec.v2errorcodes.kit import enum, validator


class TestTypedError(unittest.TestCase):
    # Omar N-3 and the header-shape revise (Ada, 10-14): one typed-error check, used by run()
    # and by the M21/M22 loops, refuses unlisted codes and array-shaped errors.
    def setUp(self) -> None:
        self.checkv2 = checkv2
        self.validators = {
            k: validator(v2, k)
            for k in (
                "creation_error",
                "restore_error",
                "wire_error",
                "loader_error",
                "compiler_error",
                "room_creation_error",
            )
        }

    def test_unlisted_codes_and_array_errors_fail(self) -> None:
        for request, response in [
            ({"op": "restore"}, {"error": "not_a_code"}),
            ({"op": "room_new"}, {"error": "not_a_code"}),
            ({"op": "compile"}, {"error": ["unknown_field", "inactivity-timeout"]}),
            ({"op": "compile"}, {"error": "not_a_code"}),
        ]:
            with (
                self.subTest(response=response),
                self.assertRaises(jsonschema.ValidationError),
            ):
                self.checkv2.typed_error(self.validators, request, response)

    def test_header_errors_take_the_compiler_shape(self) -> None:
        self.assertLessEqual(
            {"unknown_field", "missing_field", "duplicate_field"},
            enum(v2, "compiler_error", "properties", "code"),
        )
        self.checkv2.typed_error(
            self.validators,
            {"op": "compile"},
            {
                "error": {
                    "code": "unknown_field",
                    "line": 5,
                    "detail": "inactivity-timeout",
                }
            },
        )

    def test_m21_and_m22_loops_use_the_typed_check(self) -> None:
        source = inspect.getsource(self.checkv2.main)
        for marker in ("fail_closed_cases():", "safety_cases():"):
            with self.subTest(loop=marker):
                loop = source.split("for case in " + marker, 1)[1].split("\n # ", 1)[0]
                self.assertIn("typed_error(validators", loop)
