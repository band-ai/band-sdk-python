from __future__ import annotations

from types import SimpleNamespace

from band.adapters.gemini import GeminiAdapter
from band.core.types import TurnUsage


class TestGeminiUsageMapping:
    def test_maps_response_usage_metadata(self):
        """GenerateContentResponse.usage_metadata maps onto TurnUsage."""
        response = SimpleNamespace(
            usage_metadata=SimpleNamespace(
                prompt_token_count=100,
                candidates_token_count=20,
                cached_content_token_count=5,
            )
        )
        assert GeminiAdapter._usage_from_response(response) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=0,
        )

    def test_response_without_usage_is_empty(self):
        """A response without usage_metadata yields empty usage."""
        assert (
            GeminiAdapter._usage_from_response(SimpleNamespace(usage_metadata=None))
            == TurnUsage()
        )
