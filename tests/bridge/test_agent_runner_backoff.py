"""Regression for the jitter-is-a-bool bug: ``ReconnectConfig.jitter`` is a *fraction*, so 0.25 and 1.0 must produce visibly different sleep windows."""

from __future__ import annotations

from bridge_core.config import ReconnectConfig

from tests.bridge.kit import (
    build_runner,
)


class TestAgentRunnerBackoff:
    """Regression for the jitter-is-a-bool bug: ``ReconnectConfig.jitter`` is a
    *fraction*, so 0.25 and 1.0 must produce visibly different sleep windows.
    """

    async def test_zero_jitter_is_fixed_delay(self) -> None:
        runner, _, _ = build_runner()
        runner._reconnect = ReconnectConfig(jitter=0.0)
        # No randomness in the [0, 0] interval — always exactly delay.
        assert runner._backoff_sleep_seconds(8.0) == 8.0

    async def test_partial_jitter_clamps_to_min_window(self) -> None:
        runner, _, _ = build_runner()
        runner._reconnect = ReconnectConfig(jitter=0.25)
        # 75% of delay is the fixed floor; only the top 25% randomizes.
        samples = [runner._backoff_sleep_seconds(8.0) for _ in range(200)]
        assert all(6.0 <= s <= 8.0 for s in samples)

    async def test_full_jitter_spans_zero_to_delay(self) -> None:
        runner, _, _ = build_runner()
        runner._reconnect = ReconnectConfig(jitter=1.0)
        samples = [runner._backoff_sleep_seconds(8.0) for _ in range(200)]
        assert all(0.0 <= s <= 8.0 for s in samples)
        # With 200 samples on [0, 8] we should see something well below the
        # 0.25-jitter floor of 6.0 — the previous bool-treatment made these
        # two configs identical.
        assert min(samples) < 6.0
