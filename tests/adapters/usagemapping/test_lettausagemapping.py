from __future__ import annotations

from types import SimpleNamespace

from band.adapters.letta import LettaAdapter
from band.core.types import TurnUsage


class TestLettaUsageMapping:
    def test_maps_terminal_usage_statistics(self):
        statistics = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            cached_input_tokens=5,
            cache_write_tokens=3,
        )
        assert LettaAdapter._usage_from_statistics(statistics) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_missing_usage_statistics_are_empty(self):
        assert LettaAdapter._usage_from_statistics(None) == TurnUsage()
