from __future__ import annotations

import unittest

from remote_agent.band_format import FormatError, format_bytes, format_markdown_bytes


class TestSourceSize(unittest.TestCase):
    def test_a_body_over_the_bound_is_refused_before_any_scan(self) -> None:
        header = (
            b"---\nname: big\ndescription: Big.\nengine: 0.2.0-lab.1\nroles: [a]\n---\n"
        )
        at_cap = b"%%" + b"x" * (262_144 - 3) + b"\n"
        self.assertEqual(len(at_cap), 262_144)
        format_bytes(header + at_cap)
        format_bytes(at_cap)
        for source in (
            header + at_cap + b"\n",
            at_cap + b"\n",
            at_cap + b"\n\xff",
            b"\r" * 262_145,
        ):
            with self.assertRaises(FormatError) as raised:
                format_bytes(source)
            self.assertEqual(raised.exception.code, "source_size")

    def test_a_band_fence_over_the_bound_in_markdown_still_formats(self) -> None:
        fence = b"contract @1h => expired\nstate   new\n" + b"%% c\n" * 60_000
        self.assertGreater(len(fence), 262_144)
        out = format_markdown_bytes(b"```band\n" + fence + b"```\n")
        self.assertIn(b"\nstate new\n", out)
