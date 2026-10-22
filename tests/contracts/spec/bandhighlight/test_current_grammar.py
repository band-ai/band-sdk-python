"""CurrentGrammar specification regression checks."""

from __future__ import annotations

import unittest

from contracts.spec.tools import bandhighlight as bh
from tests.contracts.spec.bandhighlight.kit import (
    PROBE,
    RECORD,
    band_blocks,
    keyword_words,
    text_of,
)


class TestCurrentGrammar(unittest.TestCase):
    """Current mode: the deck's classes and look, today's keywords, no removed forms."""

    def test_total_and_quorum_are_keywords(self) -> None:
        out = bh.highlight(
            "state reviewing @30m => stalled total @1h => stalled\n"
            "  collect reports from lanes -> lead @10m => checking\n"
            "    quorum all => short\n    quorum required => short\n"
        )
        self.assertTrue(
            {"total", "quorum", "all", "from", "collect", "state"} <= keyword_words(out)
        )
        self.assertIn('<span class="tok-duration">@1h</span>', out)
        self.assertNotIn('<span class="tok-keyword">required</span>', out)

    def test_mention_caps_are_keywords(self) -> None:
        out = bh.highlight(
            "contract @1h mentions 8 => expired\nstate new\n  a -> b: k() as t mention 1 => s\n"
        )
        self.assertTrue({"mentions", "mention", "as"} <= keyword_words(out))

    def test_reply_alternatives_mark_awaits_and_operators(self) -> None:
        # op-wcvgonpv: syntax U (Eric decision 4). awaits is a keyword after the call; => | & are
        # operators; the timeout is a duration; timeout is no longer a keyword anywhere.
        out = bh.highlight(
            "state new\n  lead -> deployer: deploy() as job awaits deploying\n"
            "    => ok(request=$job) & deployed\n     | @10m & stalled\n"
            "state other\n  timeout -> awaits: ping() => done\n  timeout\n"
        )
        self.assertEqual(keyword_words(out), {"state", "as", "awaits"})
        self.assertEqual(out.count('<span class="tok-keyword">awaits</span>'), 1)
        self.assertEqual(out.count('<span class="tok-operator">&amp;</span>'), 2)
        self.assertIn('<span class="tok-operator">|</span>', out)
        self.assertIn('<span class="tok-duration">@10m</span>', out)

    def test_removed_forms_are_not_keywords(self) -> None:
        out = bh.highlight(
            "STATE new\n  due all of {a,b} with review in 5m => stalled\n"
            "  a -> b: k() activity => x\n  collect r from a -> b @1m => c\n    failure f()\n"
        )
        self.assertEqual(keyword_words(out), {"collect", "from"})

    def test_every_band_block_keeps_its_copyable_text(self) -> None:
        for path, line, src in band_blocks() + [("probe", 1, PROBE)]:
            with self.subTest(block=f"{path}:{line}"):
                self.assertEqual(text_of(bh.highlight(src)), src)
                self.assertEqual(text_of(bh.render_block(src)), src)

    def test_probe_marks_exactly_the_recorded_keywords(self) -> None:
        self.assertEqual(keyword_words(bh.highlight(PROBE)), set(RECORD["keywords"]))
        self.assertEqual(set(bh.KEYWORDS), set(RECORD["keywords"]))

    def test_styles_are_the_deck_token_colors(self) -> None:
        for rule in (
            ".tok-keyword{color:#c4b5fd;font-weight:600}",
            ".tok-type,.tok-field{color:#93c5fd}",
            ".tok-duration,.tok-number{color:#fdba74}",
            ".tok-reference{color:#67e8f9}",
            ".tok-string{color:#b7dfb5}",
            ".tok-comment{color:#9daec2;font-style:italic}",
            ".tok-operator{color:#d4dce6}",
        ):
            self.assertIn(rule, bh.CSS)
