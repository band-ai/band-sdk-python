from __future__ import annotations

import unittest

from remote_agent.band_format import format_markdown_bytes


class TestMarkdownClosingFence(unittest.TestCase):
    def test_whitespace_heavy_closer_is_preserved_while_many_lines_format(self) -> None:
        closing = b"```" + b" " * 4000 + b"\n"
        source = b"```band\ntype a\n" + b"v:x\n" * 200 + closing
        expected = b"```band\ntype a\n" + b"  v: x\n" * 200 + closing
        self.assertEqual(format_markdown_bytes(source), expected)
        self.assertEqual(format_markdown_bytes(expected), expected)
