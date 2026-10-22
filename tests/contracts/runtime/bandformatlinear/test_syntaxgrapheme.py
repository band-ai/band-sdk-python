from __future__ import annotations

import unittest

from remote_agent.band_format import _plain
from remote_agent.graphemes import boundaries, table_data


class TestSyntaxGrapheme(unittest.TestCase):
    def test_only_standalone_unicode16_graphemes_are_syntax(self) -> None:
        # Independent Unicode-16 boundary engine, including each property range
        # endpoint. Syntax must not split a cluster or change nesting for one.
        neighbors = {"a", "\n", "é", "\u200d", "\U0001f469", "\U0001f1e6"}
        neighbors.update(
            chr(cp)
            for first, last, _ in table_data()["gcb"]
            for cp in (first, last)
            if cp > 127
        )
        for neighbor in neighbors:
            for token in "()|&":
                for text, at in (
                    ("a" + token + neighbor + "z", 1),
                    ("a" + neighbor + token + "z", 1 + len(neighbor)),
                ):
                    cuts = boundaries(text)
                    standalone = at in cuts and at + 1 in cuts
                    for depth in (0, 2):
                        expected_depth = depth
                        expected = text
                        if standalone and token == "(":
                            expected_depth += 1
                        elif standalone and token == ")":
                            expected_depth = max(0, depth - 1)
                        elif standalone and depth == 0:
                            expected = text[:at] + " " + token + " " + text[at + 1 :]
                        self.assertEqual(
                            _plain(text, depth),
                            (expected, expected_depth),
                            (repr(text), depth),
                        )
