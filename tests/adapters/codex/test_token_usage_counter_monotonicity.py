"""Codex adapter: codexTokenUsage protection against non-monotonic cumulative updates."""

from __future__ import annotations

import logging

import pytest

from band.integrations.codex.types import CodexTokenUsage


class TestTokenUsageCounterMonotonicity:
    """CodexTokenUsage protection against non-monotonic cumulative updates."""

    def test_token_usage_warns_on_non_monotonic_counters(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A decreasing cumulative counter triggers a warning.

        After the adapter anchors a new turn (``reset_turn_deltas``), a
        late event from the previous turn with a smaller cumulative must
        leave the turn deltas clamped to 0 rather than going negative.
        """

        usage = CodexTokenUsage()
        usage.update({"usage": {"inputTokens": 100, "outputTokens": 100}})
        # Adapter anchors the new turn at cumulative=100/100.
        usage.reset_turn_deltas()
        with caplog.at_level(logging.WARNING, logger="band.integrations.codex.types"):
            usage.update({"usage": {"inputTokens": 50, "outputTokens": 50}})
        assert any(
            "token usage counter decreased" in record.message.lower()
            for record in caplog.records
        )
        # Cumulative stays at 100 (monotonic); turn delta clamped to 0.
        assert usage.input_tokens == 100
        assert usage.output_tokens == 100
        assert usage.turn_input_tokens == 0
        assert usage.turn_output_tokens == 0
