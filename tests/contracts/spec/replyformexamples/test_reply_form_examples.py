"""ReplyFormExamples specification regression checks."""

from __future__ import annotations

import unittest

from tests.contracts.spec.replyformexamples.kit import spec_hits


class TestReplyFormExamples(unittest.TestCase):
    def test_no_single_recipient_send_then_answer_pair_is_left(self) -> None:
        self.assertEqual(
            spec_hits(group=False), [], "write the request with reply alternatives"
        )

    def test_group_requests_wait_for_the_group_design(self) -> None:
        # defaults-v2 (roster) and lowercase-v2 ({a,b}); revisit when Rafael's group design lands.
        self.assertEqual(
            sorted({hit.split(":")[0] for hit in spec_hits(group=True)}),
            ["defaults-v2.md", "lowercase-v2.md"],
        )
