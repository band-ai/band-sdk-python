"""Codex adapter: per turn token usage."""

from __future__ import annotations

from band.integrations.codex.types import CodexTokenUsage


class TestPerTurnTokenUsage:
    def test_token_usage_computes_per_turn_deltas(self) -> None:
        """Per-turn deltas are computed from consecutive cumulative updates."""

        usage = CodexTokenUsage()

        # First update: turn 1
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "reasoningTokens": 200,
                    "totalTokens": 1700,
                }
            }
        )
        assert usage.turn_input_tokens == 1000
        assert usage.turn_output_tokens == 500
        assert usage.turn_total_tokens == 1700

        # Second update: turn 2 (cumulative increases)
        usage.reset_turn_deltas()
        usage.update(
            {
                "usage": {
                    "inputTokens": 2500,
                    "outputTokens": 900,
                    "reasoningTokens": 400,
                    "totalTokens": 3800,
                }
            }
        )
        assert usage.turn_input_tokens == 1500  # 2500 - 1000
        assert usage.turn_output_tokens == 400  # 900 - 500
        assert usage.turn_reasoning_tokens == 200  # 400 - 200
        assert usage.turn_total_tokens == 2100  # 3800 - 1700

    def test_token_usage_metadata_includes_turn_deltas(self) -> None:
        """to_metadata() includes per-turn deltas when available."""

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "totalTokens": 1500,
                }
            }
        )
        meta = usage.to_metadata()
        assert meta["codex_turn_input_tokens"] == 1000
        assert meta["codex_turn_total_tokens"] == 1500

    def test_token_usage_format_summary_includes_turn(self) -> None:
        """format_summary() shows per-turn breakdown when deltas > 0."""

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "totalTokens": 1500,
                }
            }
        )
        summary = usage.format_summary()
        assert "turn:" in summary
        assert "+1,000 in" in summary

    def test_reset_turn_deltas(self) -> None:
        """reset_turn_deltas() zeroes out per-turn counters."""

        usage = CodexTokenUsage()
        usage.update(
            {"usage": {"inputTokens": 1000, "outputTokens": 500, "totalTokens": 1500}}
        )
        assert usage.turn_total_tokens == 1500
        usage.reset_turn_deltas()
        assert usage.turn_total_tokens == 0
        assert usage.turn_input_tokens == 0

        # Thread-level totals should be unchanged
        assert usage.total_tokens == 1500

    def test_multi_event_turn_delta_is_cumulative_from_anchor(self) -> None:
        """A turn with multiple tokenUsage events reports the running turn total.

        Without the turn-start anchor, each ``update()`` would overwrite
        ``turn_*`` with the per-event delta, so a turn with events at
        cumulative 150 then 180 (after resetting at 100) would end the
        turn reporting ``turn_input_tokens=30``.  With the anchor, the
        final value is ``180 - 100 = 80`` — the whole-turn rise.
        """

        usage = CodexTokenUsage()
        # End of previous turn: cumulative = 100.
        usage.update({"usage": {"inputTokens": 100, "outputTokens": 0}})
        # New turn starts — anchor captured at 100.
        usage.reset_turn_deltas()

        # First event inside the turn.
        usage.update({"usage": {"inputTokens": 150, "outputTokens": 0}})
        assert usage.turn_input_tokens == 50  # 150 - 100

        # Second event inside the same turn — must keep growing from anchor.
        usage.update({"usage": {"inputTokens": 180, "outputTokens": 0}})
        assert usage.turn_input_tokens == 80  # 180 - 100, NOT 180 - 150

        # Third event: still anchored at 100.
        usage.update({"usage": {"inputTokens": 200, "outputTokens": 0}})
        assert usage.turn_input_tokens == 100  # 200 - 100
