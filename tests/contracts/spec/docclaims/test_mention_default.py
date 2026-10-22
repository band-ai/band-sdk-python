"""MentionDefault specification regression checks."""

from __future__ import annotations

import re
import unittest

from tests.contracts.spec.docclaims.kit import ROOT, docs, prose


class TestMentionDefault(unittest.TestCase):
    """DF-13: D-3 removed the default cap; a cap exists only when `mentions N` sets one."""

    def test_no_doc_claims_a_default_one_recipient_limit(self) -> None:
        stale = re.compile(r"default (limit|cap) of one recipient", re.IGNORECASE)
        hits = [
            str(p.relative_to(ROOT))
            for p in docs("spec/**/*.md", "skills/**/*.md", "*/README.md")
            if stale.search(prose(p))
        ]
        self.assertEqual(
            hits, [], "see spec/contract-dsl.md#mention-caps: no cap unless one is set"
        )
