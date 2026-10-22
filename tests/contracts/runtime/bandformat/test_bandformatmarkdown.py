from __future__ import annotations

from remote_agent.band_format import FormatError, format_markdown_bytes
from tests.contracts.runtime.formatter import (
    FORMATTED,
    MESSY,
    BandFormatMarkdownFixture,
)


class TestBandFormatMarkdown(BandFormatMarkdownFixture):
    def test_band_fences_are_formatted_and_every_other_byte_is_kept(self) -> None:
        fenced = (
            "# Notes\n\nText   kept.  \n\n```band\n{}```\n\n```elixir\ntype   a\n```\n"
        )
        self.assertEqual(self.fmt(fenced.format(MESSY)), fenced.format(FORMATTED))
        self.assertEqual(self.fmt(fenced.format(FORMATTED)), fenced.format(FORMATTED))

    def test_an_indented_fence_is_formatted_at_its_indentation(self) -> None:
        source = (
            "1. Step\n\n   ```band\n   type a\n        b:text(1)\n\n   end x\n   ```\n"
        )
        expected = (
            "1. Step\n\n   ```band\n   type a\n     b: text(1)\n\n   end x\n   ```\n"
        )
        self.assertEqual(self.fmt(source), expected)

    def test_tilde_and_longer_fences_and_an_info_string_with_spaces(self) -> None:
        self.assertEqual(
            self.fmt("~~~ band \ntype a\nend x\n~~~~\n"),
            "~~~ band \ntype a\n\nend x\n~~~~\n",
        )
        # A shorter run inside is content, not the closing fence: it is a nested line.
        self.assertEqual(
            self.fmt("````band\ntype a\n```\nend x\n````\n"),
            "````band\ntype a\n  ```\n\nend x\n````\n",
        )
        self.assertEqual(
            self.fmt("```band\ntype a\nend x\n  ```  \n"),
            "```band\ntype a\n\nend x\n  ```  \n",
        )

    def test_fences_that_are_kept_as_written(self) -> None:
        for source in (
            "```band\n  a -> b: m()\n```\n",  # a fragment: no top-level declaration
            "```band\nname: x\nroles: [a]\n```\n",  # header fields only
            "```band\n\n   \n```\n",  # blank
            "  ```band\n  type a\n end x\n  ```\n",  # a line indented less than the fence
            "```band\ntype a\nend x\n",  # unclosed: runs to the end
            "```bandx\ntype a\nend x\n```\n",  # another info string
            "```\ntype a\n```band\ntype b\nend x\n```\n",  # nothing inside a fence is parsed
            "```band\n```\n",  # empty
        ):
            with self.subTest(source=source):
                self.assertEqual(self.fmt(source), source)

    def test_a_fence_after_a_kept_fence_is_still_formatted(self) -> None:
        self.assertEqual(
            self.fmt("```text\nx\n```\n```band\ntype a\nend x\n```"),
            "```text\nx\n```\n```band\ntype a\n\nend x\n```",
        )

    def test_crlf_and_invalid_utf8_are_refused(self) -> None:
        with self.assertRaises(FormatError) as crlf:
            format_markdown_bytes(b"# x\r\n")
        self.assertEqual(crlf.exception.code, "crlf")
        with self.assertRaises(FormatError) as encoding:
            format_markdown_bytes(b"# x\n\xff\r\n")
        self.assertEqual(encoding.exception.code, "encoding")
