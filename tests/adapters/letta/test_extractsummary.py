from __future__ import annotations

from band.adapters.letta import (
    LettaAdapter,
)


class TestExtractSummary:
    def test_short_text_kept_whole(self) -> None:
        parts = ["Hello there. This is more text."]
        assert "Hello there. This is more text." == LettaAdapter._extract_summary(parts)

    def test_truncates_on_word_boundary(self) -> None:
        parts = ["alpha beta " * 30]
        result = LettaAdapter._extract_summary(parts, max_length=50)
        assert len(result) <= 53  # 50 + "..."
        assert result.endswith("...")
        assert not result.removesuffix("...").endswith("alph")  # no mid-word cut

    def test_no_sentence_heuristics(self) -> None:
        """Decimals must not be mistaken for sentence ends."""
        parts = ["pi is 3.14 and e is 2.71"]
        assert "pi is 3.14 and e is 2.71" == LettaAdapter._extract_summary(parts)

    def test_empty(self) -> None:
        assert "" == LettaAdapter._extract_summary([])

    def test_multiple_parts_joined(self) -> None:
        parts = ["First part.", "Second part."]
        assert "First part. Second part." == LettaAdapter._extract_summary(parts)
