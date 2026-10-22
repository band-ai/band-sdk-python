"""SiteBlocks specification regression checks."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from contracts.spec.tools import bandhighlight as bh
from tests.contracts.spec.bandhighlight.kit import (
    CONTRACT_LINE,
    FENCE,
    RECORD,
    ROOT,
    TOOLS,
    band_blocks,
    fences,
    prompt_problems,
)


class TestSiteBlocks(unittest.TestCase):
    """The site's contract examples are band blocks, and every band block is highlighted."""

    def test_contract_examples_are_band_blocks(self) -> None:
        plain = [
            f"{p}:{n}"
            for p, n, info, src in fences()
            if info in ("", "text") and CONTRACT_LINE.search(src)
        ]
        self.assertEqual(
            plain, [], "label contract source ```band so the site highlights it"
        )
        self.assertGreaterEqual(len(band_blocks()), 30)

    def test_every_band_block_is_highlighted(self) -> None:
        self.assertEqual(bh.check_tree(ROOT / "spec"), [])

    def test_unhighlighted_band_block_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "spec"
            shutil.copytree(
                TOOLS, src / "tools", ignore=shutil.ignore_patterns("__pycache__")
            )
            (src / "page.md").write_text(
                "# Page\n\n```band\n   \n```\n", encoding="utf-8"
            )
            self.assertEqual(
                [e.split(": ")[0] for e in bh.check_tree(src)], ["page.md:3"]
            )

    def test_keyword_drift_fails_the_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "spec"
            shutil.copytree(
                TOOLS, src / "tools", ignore=shutil.ignore_patterns("__pycache__")
            )
            record = dict(
                RECORD, keywords=sorted(set(RECORD["keywords"]) - {"quorum"} | {"due"})
            )
            (src / "tools/band-keywords.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            errors = bh.check_tree(src)
            self.assertEqual(len(errors), 1)
            self.assertRegex(errors[0], r"keyword drift: .*due.*quorum")

    def test_reply_examples_are_plain_band_blocks(self) -> None:
        # op-3xbxj7hg: the compiler parses syntax U (Kenji ccf3a54), so reply examples are fenced
        # exactly ```band like every other example: mix contract.format, --require-prompts and the
        # >= 38 count in SpecBandBlocksFormatTest all take them. No other band info string remains.
        replies = []
        for path in sorted((ROOT / "spec").rglob("*.md")):
            lines = path.read_text(encoding="utf-8").split("\n")
            for i, line in enumerate(lines):
                m = FENCE.match(line)
                if not m or m["info"].strip().split(" ")[0] != "band":
                    continue
                where = f"{path.relative_to(ROOT)}:{i + 1}"
                self.assertEqual(m["info"].strip(), "band", where)
                body = lines[i + 1 : lines.index("```", i + 1)]
                if any(re.match(r"\s*\| @[0-9]+[smhd] & ", b) for b in body):
                    replies.append(where)
        self.assertGreaterEqual(len(replies), 4, replies)

    def test_every_example_prompts_its_contract_roles_types_and_states(self) -> None:
        # spec/conformance/format/ holds the formatter's input/expected fixtures, not examples.
        problems = [
            p
            for path, line, src in band_blocks()
            if not path.is_relative_to("spec/conformance/format")
            for p in prompt_problems(f"{path}:{line}", src)
        ]
        self.assertEqual(problems, [], "see prompt_problems: Eric 10-15 prompt rule")

    def test_rendered_block_is_static_spans_in_a_deck_codeblock(self) -> None:
        out = bh.render_block("state new\n  a -> b: k() => done\n")
        self.assertTrue(
            out.startswith('<pre class="band-hl"><code class="language-band">')
        )
        self.assertNotIn("<script", out)
