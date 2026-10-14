"""Codex adapter: late events with smaller cumulative counters must not rewind state."""

from __future__ import annotations

from band.integrations.codex.types import CodexTokenUsage


class TestTokenUsageCumulativeMonotonicity:
    """Late events with smaller cumulative counters must not rewind state.

    Without the max-preserving guard, a late ``thread/tokenUsage/updated``
    from the previous turn can overwrite the cumulative totals with a
    smaller value.  The next real event of the current turn then computes
    ``turn_delta = new - (rewound)`` and double-counts the gap.
    """

    def test_late_smaller_event_does_not_corrupt_next_delta(self) -> None:

        usage = CodexTokenUsage()
        # End of previous turn: cumulative = 100.
        usage.update({"usage": {"inputTokens": 100, "outputTokens": 0}})
        assert usage.input_tokens == 100

        # Adapter starts a new turn.
        usage.reset_turn_deltas()

        # Late event from the previous turn arrives with a smaller cumulative.
        usage.update({"usage": {"inputTokens": 80, "outputTokens": 0}})
        # Cumulative must stay at 100 (monotonic), turn delta clamped to 0.
        assert usage.input_tokens == 100
        assert usage.turn_input_tokens == 0

        # First real event of the new turn: cumulative = 120.
        usage.update({"usage": {"inputTokens": 120, "outputTokens": 0}})
        # Turn delta is 120 - 100 = 20, NOT 120 - 80 = 40.
        assert usage.turn_input_tokens == 20
        assert usage.input_tokens == 120
