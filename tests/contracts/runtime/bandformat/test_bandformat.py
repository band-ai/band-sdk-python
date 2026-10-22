from __future__ import annotations

import unittest

from remote_agent.band_format import (
    FormatError,
    format_bytes,
    format_markdown_bytes,
    format_source,
)
from tests.contracts.runtime.formatter import FORMATTED, MESSY, vector_inputs


class TestBandFormat(unittest.TestCase):
    def test_formats_layout_keeping_comments_notes_docs_sigils_and_selectors(
        self,
    ) -> None:
        self.assertEqual(format_source(MESSY), FORMATTED)
        self.assertEqual(format_source(FORMATTED), FORMATTED)

    def test_collect_options_events_and_room_rules_sit_under_their_scope(self) -> None:
        messy = (
            "contract @30m => expired\ntype reviewer: agent\nreviewers: list(reviewer)\n"
            "type review\ntype ping\nstate reviewing\nevent reviewers -> lead: ping()\n"
            "collect reports from reviewers -> lead @5m => done\nreply review()\nanystate\n"
            "lead -> reviewers: ping()\nend done\n"
        )
        expected = (
            "contract @30m => expired\n\ntype reviewer: agent\n\nreviewers: list(reviewer)\n\n"
            "type review\n\ntype ping\n\nstate reviewing\n  event reviewers -> lead: ping()\n"
            "  collect reports from reviewers -> lead @5m => done\n    reply review()\n\n"
            "anystate\n  lead -> reviewers: ping()\n\nend done\n"
        )
        self.assertEqual(format_source(messy), expected)

    def test_trailing_blanks_missing_newline_and_blank_runs_are_normalized(
        self,
    ) -> None:
        self.assertEqual(
            format_source("\n\ncontract @1h => expired   \n\n\n\nend done"),
            "contract @1h => expired\n\nend done\n",
        )
        self.assertEqual(
            format_source("contract @1h => expired\n\n\n"), "contract @1h => expired\n"
        )

    def test_a_body_without_frontmatter_is_formatted_as_a_body(self) -> None:
        self.assertEqual(
            format_source(
                "contract @infinity => expired\ntype offer\n task: text(600)\n"
            ),
            "contract @infinity => expired\n\ntype offer\n  task: text(600)\n",
        )

    def test_a_multiline_doc_that_cannot_shift_uniformly_is_kept_byte_for_byte(
        self,
    ) -> None:
        body = (
            'type review\n    """\n Less indented than the shift.\n    """\n'
            "  decision: enum(approve|revise)\n"
        )
        self.assertEqual(format_source(body), body)

    def test_crlf_and_invalid_utf8_are_refused_rather_than_rewritten(self) -> None:
        with self.assertRaises(FormatError) as crlf:
            format_bytes(b"contract @1h => expired\r\n")
        self.assertEqual(crlf.exception.code, "crlf")
        with self.assertRaises(FormatError) as encoding:
            format_bytes(b"contract @1h => expired\n\xff\n")
        self.assertEqual(encoding.exception.code, "encoding")

    def test_shared_vectors_format_input_to_expected_and_expected_is_a_fixed_point(
        self,
    ) -> None:
        cases = sorted(vector_inputs())
        # A floor, not a pin on the last vector: 01-09 on Kenji's 95c9b25, 09 is Markdown.
        names = [c.parent.name for c in cases]
        self.assertGreaterEqual(len(cases), 9)
        self.assertIn("08-prompt-blocks", names)
        self.assertTrue(any(c.suffix == ".md" for c in cases))
        for case in cases:
            with self.subTest(case=case.parent.name):
                fmt = format_markdown_bytes if case.suffix == ".md" else format_bytes
                expected = (case.parent / ("expected" + case.suffix)).read_bytes()
                self.assertEqual(fmt(case.read_bytes()), expected)
                self.assertEqual(fmt(expected), expected)

    def test_one_line_prompts_become_blocks_with_the_same_text(self) -> None:
        cases = [
            ('"Plain."\nrole lead\n', '"""\nPlain.\n"""\nrole lead\n'),
            (
                '  "Escapes \\"kept\\"."  \nrole lead\n',
                '"""\nEscapes \\"kept\\".\n"""\nrole lead\n',
            ),
            ('~s"C:\\work"\nrole lead\n', '~s"""\nC:\\work\n"""\nrole lead\n'),
            (
                "~s(No escapes: 100%.)\nrole lead\n",
                '"""\nNo escapes: 100%.\n"""\nrole lead\n',
            ),
            ("~s[a #{b}]\nrole lead\n", '~s"""\na #{b}\n"""\nrole lead\n'),
            (
                'type a\n    "Nested."\n  b: text(1)\n',
                'type a\n  """\n  Nested.\n  """\n  b: text(1)\n',
            ),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(format_source(source), expected)
                self.assertEqual(format_source(expected), expected)

    def test_a_one_line_prompt_that_cannot_be_a_block_is_kept(self) -> None:
        for doc in (
            '""',
            '"a" b',
            '"unterminated',
            '"trailing\\',
            "~s()",
            '~s(""")',
            '~s( """ )',
            "~s(a)b",
            "~s(a)(b)",
            "~sx",
            '"a\\"',
        ):
            with self.subTest(doc=doc):
                source = doc + "\nrole lead\n"
                self.assertEqual(format_source(source), source)

    def test_a_prompt_sits_directly_above_its_item(self) -> None:
        self.assertEqual(
            format_source('"""\nDoc.\n"""\n\n\nrole lead\n"Field."\n\ntype a\n'),
            '"""\nDoc.\n"""\nrole lead\n\n"""\nField.\n"""\ntype a\n',
        )
        # A comment between the prompt and the item keeps the blank line after it.
        self.assertEqual(
            format_source('"Doc."\n%% note\n\nrole lead\n'),
            '"""\nDoc.\n"""\n%% note\n\nrole lead\n',
        )

    def test_one_blank_line_between_every_top_level_declaration(self) -> None:
        self.assertEqual(
            format_source(
                "type a\ntype b\nend x\nend y\nrole r\nrole s\nx: list(y)\nz: list(y)\n"
            ),
            "type a\n\ntype b\n\nend x\n\nend y\n\nrole r\n\nrole s\n\nx: list(y)\n\nz: list(y)\n",
        )
