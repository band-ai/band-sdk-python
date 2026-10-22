"""M4: current file sources reject the retired native format."""

from __future__ import annotations

import json
import unittest

import jsonschema

from contracts.spec.tools.buildv2schemas import root as generated
from tests.paths import CONTRACTS_ROOT as ROOT


class TestCurrentSourceFormat(unittest.TestCase):
    def test_native_is_rejected_by_generated_and_checked_in_current_schema(
        self,
    ) -> None:
        source = {
            "profile": "band-contract/file-source/1",
            "format": "native",
            "content": "retired input",
            "sha256": "0" * 64,
        }
        for schema in (
            generated,
            json.loads((ROOT / "spec/schemas/protocol-v2.schema.json").read_text()),
        ):
            with self.subTest(schema=schema["$id"]):
                validator = jsonschema.Draft202012Validator(
                    {**schema, "$ref": "#/$defs/source"}
                )
                self.assertFalse(validator.is_valid(source))
                self.assertTrue(validator.is_valid({**source, "format": "frontmatter"}))
                self.assertTrue(
                    validator.is_valid(
                        {
                            **source,
                            "format": "standard",
                            "body": "body",
                            "package_name": "package",
                        }
                    )
                )


if __name__ == "__main__":
    unittest.main()
