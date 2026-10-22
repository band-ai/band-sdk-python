from __future__ import annotations

import unittest

from remote_agent.band_format import _unescape, missing_prompts


class TestLinearEscape(unittest.TestCase):
    def test_escape_decoding_preserves_bounded_hex_and_unknown_forms(self) -> None:
        self.assertEqual(
            _unescape(r"\u0020\u{a}\x09\u{110000}\q\u12\x0\u{1234567}" + "\\"),
            " \n\t\U0010ffffqu12x0u{1234567}\\",
        )
        self.assertEqual(_unescape("a\\\nb\\\\c"), "ab\\c")

    def test_many_escapes_still_classify_a_blank_prompt(self) -> None:
        header = b"---\nname: scaling\nroles: []\n---\n"
        source = header + ('"' + r"\u0020\u{a}\x09\s" * 2000 + '"\ntype a\n').encode()
        self.assertEqual(
            missing_prompts(source),
            [
                {"item": "contract", "name": "scaling", "line": 1, "file_line": 5},
                {"item": "type", "name": "a", "line": 1, "file_line": 5},
            ],
        )

    def test_literal_slashes_cannot_create_new_simple_or_hex_escapes(self) -> None:
        cases = [
            (r"\\n", r"\n"),
            (r"\\\n", "\\\n"),
            (r"\\u0020", r"\u0020"),
            (r"\\\u0020", "\\ "),
            ("\\\n\\", "\\"),
            (r"\q\s\t\n", "q \t\n"),
        ]
        for source, expected in cases:
            self.assertEqual(_unescape(source), expected, repr(source))
