from __future__ import annotations

import unittest

from remote_agent.band_format import missing_prompts


class TestPromptOrdering(unittest.TestCase):
    def test_source_order_defaults_and_later_declaration_metadata_are_preserved(
        self,
    ) -> None:
        source = (
            b"---\nname: ordering\nroles: [z, a]\n---\n"
            b"type z\ntype a\nstate z\nstate a\ntype z\n"
        )
        expected = [
            ("contract", "ordering", 1),
            ("role", "z", 1),
            ("role", "a", 1),
            ("type", "a", 2),
            ("state", "z", 3),
            ("state", "a", 4),
            ("type", "z", 5),
        ]
        self.assertEqual(
            missing_prompts(source),
            [
                {"item": kind, "name": name, "line": line, "file_line": line + 4}
                for kind, name, line in expected
            ],
        )
