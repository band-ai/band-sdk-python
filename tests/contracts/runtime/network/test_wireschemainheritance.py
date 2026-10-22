from __future__ import annotations

import json
import unittest

from tests.paths import CONTRACTS_ROOT


class TestWireSchemaInheritance(unittest.TestCase):
    def test_compact_schemas_preserve_every_final_registry_extension_field(
        self,
    ) -> None:
        root = CONTRACTS_ROOT / "spec/schemas"
        for name in ("v2", "coordination", "participants", "contributions", "replies"):
            with self.subTest(registry=name):
                definitions = json.loads(
                    (root / ("protocol-" + name + ".schema.json")).read_text()
                )["$defs"]
                full, compact = definitions["view"], definitions["http_compact_view"]
                self.assertEqual(
                    compact["properties"],
                    {
                        key: value
                        for key, value in full["properties"].items()
                        if key != "definition"
                    },
                )
                self.assertEqual(
                    compact["required"],
                    [key for key in full["required"] if key != "definition"],
                )
                self.assertFalse(compact["additionalProperties"])
