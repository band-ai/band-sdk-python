"""InactivityTimeoutSchema specification regression checks."""

from __future__ import annotations

import unittest
from typing import Any

import jsonschema

from contracts.spec.tools.buildcontributionschemas import root as contributions
from contracts.spec.tools.buildv2schemas import root as v2


class TestInactivityTimeoutSchema(unittest.TestCase):
    def test_v2_card_inactivity_timeout_is_infinite_only(self) -> None:
        # The header field was removed (defaults-v2.md); the normalized card keeps only `infinite`.
        schemas: tuple[dict[str, Any], ...] = (v2, contributions)
        for schema in schemas:
            with self.subTest(schema=schema["$id"]):
                field = jsonschema.Draft202012Validator(
                    {
                        "$defs": schema["$defs"],
                        **schema["$defs"]["card"]["properties"]["inactivity_timeout"],
                    }
                )
                self.assertTrue(field.is_valid("infinite"))
                self.assertFalse(field.is_valid(["seconds", 300]))
