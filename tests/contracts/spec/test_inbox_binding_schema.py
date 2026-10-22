"""K2: the standalone journal-head extension cannot change the shared Band page."""

from __future__ import annotations

import copy
import json
import unittest
from typing import Any

import jsonschema

from contracts.spec.tools.buildschemas import root as v1_root
from contracts.spec.tools.buildv2schemas import root as v2_root
from tests.paths import CONTRACT_SPEC_ROOT

# The v1 builder, the v2 builder and the committed v2 bundle implementations read (N-8).
REGISTRIES = {
    "v1": v1_root,
    "v2": v2_root,
    "v2-committed": json.loads(
        (CONTRACT_SPEC_ROOT / "schemas/protocol-v2.schema.json").read_text()
    ),
}


class TestInboxBindingSchema(unittest.TestCase):
    def validator(
        self, root: dict[str, Any], name: str
    ) -> jsonschema.Draft202012Validator:
        return jsonschema.Draft202012Validator(
            {
                "$schema": root["$schema"],
                "$defs": root["$defs"],
                "$ref": "#/$defs/" + name,
            }
        )

    def test_shared_page_has_no_standalone_head(self) -> None:
        page = {"cursor": 0, "events": []}
        for label, root in REGISTRIES.items():
            with self.subTest(registry=label):
                validator = self.validator(root, "inbox_response")
                self.assertTrue(validator.is_valid(page))
                self.assertFalse(
                    validator.is_valid(
                        {**page, "head": {"sequence": 0, "hash": "0" * 64}}
                    )
                )

    def test_standalone_requires_the_complete_journal_head(self) -> None:
        for label, root in REGISTRIES.items():
            with self.subTest(registry=label):
                self.assertIn("standalone_inbox_response", root["$defs"])
                validator = self.validator(root, "standalone_inbox_response")
                page = {
                    "cursor": 0,
                    "events": [],
                    "head": {"sequence": 0, "hash": "0" * 64},
                }
                self.assertTrue(validator.is_valid(page))
                missing = copy.deepcopy(page)
                missing.pop("head")
                self.assertFalse(validator.is_valid(missing))
                for bad_head in (
                    {"sequence": 0},
                    {"hash": "0" * 64},
                    {"sequence": True, "hash": "0" * 64},
                ):
                    self.assertFalse(validator.is_valid({**page, "head": bad_head}))


if __name__ == "__main__":
    unittest.main()
