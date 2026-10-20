from __future__ import annotations

from types import SimpleNamespace

from band.adapters.google_adk import GoogleADKAdapter
from band.core.types import TurnUsage


class TestGoogleADKUsageMapping:
    def test_maps_event_usage_metadata(self):
        """ADK event.usage_metadata maps onto TurnUsage (no cache-write dim)."""
        event = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=20,
                cached_content_token_count=5,
            )
        )
        assert GoogleADKAdapter._usage_from_event(event) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=0,
        )

    def test_event_without_usage_is_empty(self):
        """A non-model event (no usage_metadata) contributes empty usage."""
        assert (
            GoogleADKAdapter._usage_from_event(SimpleNamespace(usage_metadata=None))
            == TurnUsage()
        )
