from __future__ import annotations

import unittest

from remote_agent.band_format import format_bytes, missing_prompts
from tests.contracts.runtime.formatter import (
    PROMPTS_COMPLETE,
    PROMPTS_HEADER,
    PROMPTS_PARTIAL,
    PROMPTS_PARTIAL_MISSING,
    ROOT,
    band_files,
)


class TestBandPrompts(unittest.TestCase):
    def test_a_contract_with_every_prompt_misses_none(self) -> None:
        self.assertEqual(missing_prompts(PROMPTS_COMPLETE.encode()), [])

    def test_lists_each_missing_prompt_with_its_item_name_line_and_file_line(
        self,
    ) -> None:
        self.assertEqual(
            missing_prompts(PROMPTS_PARTIAL.encode()), PROMPTS_PARTIAL_MISSING
        )

    def test_what_each_prompt_documents(self) -> None:
        header = PROMPTS_HEADER.replace(
            "roles: [lead, worker]", "roles: [lead, workers]"
        )
        body = (
            '"C"\ncontract @1h => expired\n"L"\nrole lead\ntype worker: agent\n"W"\nworkers: list(worker)\n'
            # A prompt under `type offer`, before its field, documents the field.
            'type offer\n  "The field."\n  text: text(1)\n'
            # A blank or whitespace-only prompt is no prompt; its line is the prompt's.
            '"""\n   \n"""\ntype note\n" "\nstate new\n  "Rule."\n  lead -> workers: offer() => done\n'
            "/* note\n */\n%% comment\n~s(Raw.)\nstate idle\n  lead -> workers: note() => done\n"
            "end done\n"
        )
        self.assertEqual(
            missing_prompts((header + body).encode()),
            [
                {"item": "type", "name": "offer", "line": 8, "file_line": 14},
                {"item": "type", "name": "note", "line": 11, "file_line": 17},
                {"item": "state", "name": "new", "line": 15, "file_line": 21},
            ],
        )

    def test_a_body_without_frontmatter_is_not_a_contract_to_check(self) -> None:
        self.assertIsNone(missing_prompts(b"contract @1h => expired\nend done\n"))

    def test_every_repo_band_file_is_idempotent(self) -> None:
        files = band_files()
        self.assertGreaterEqual(len(files), 56)
        for path in files:
            with self.subTest(path=str(path.relative_to(ROOT))):
                once = format_bytes(path.read_bytes())
                self.assertEqual(format_bytes(once), once)
