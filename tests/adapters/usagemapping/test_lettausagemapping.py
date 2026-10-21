from __future__ import annotations

from types import SimpleNamespace

from band.adapters.letta import LettaAdapter
from band.core.types import TurnUsage


class TestLettaUsageMapping:
    def test_maps_response_usage(self):
        """LettaResponse.usage (per_room mode) maps onto TurnUsage."""
        response = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=100,
                completion_tokens=20,
                cached_input_tokens=5,
                cache_write_tokens=3,
            )
        )
        assert LettaAdapter._usage_from_response(response) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_response_without_usage_is_empty(self):
        """A response with no usage (e.g. shared-mode stream) yields empty usage."""
        assert (
            LettaAdapter._usage_from_response(SimpleNamespace(usage=None))
            == TurnUsage()
        )
