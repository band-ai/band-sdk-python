from __future__ import annotations

import unittest

from remote_agent.band_format import _segments, format_source


class TestLinearSigil(unittest.TestCase):
    def test_adjacent_sigils_escaped_delimiters_and_unclosed_tail(self) -> None:
        sigils = [
            r"~r/a\/b/",
            "~s|a|",
            "~w{b}",
            "~s[c]",
            "~r(d)",
            "~s<é>",
            '~s"f"',
            "~s'g'",
        ]
        self.assertEqual(
            _segments(("prefix " + "".join(sigils) + " tail ~s[open->x").encode()),
            [
                ("plain", "prefix "),
                *[("sigil", s) for s in sigils],
                ("plain", " tail "),
                ("sigil", "~s[open->x"),
            ],
        )

    def test_many_adjacent_sigils_keep_contents_and_byte_boundaries(self) -> None:
        sigils = "~r/x/" * 2000 + "~s[é->x|y&z]"
        self.assertEqual(
            format_source("type a\nv:" + sigils), "type a\n  v: " + sigils + "\n"
        )
