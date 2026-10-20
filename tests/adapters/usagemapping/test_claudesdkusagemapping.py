from __future__ import annotations

from types import SimpleNamespace

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.core.types import TurnUsage


class TestClaudeSDKUsageMapping:
    def test_maps_usage_dict(self):
        """ResultMessage.usage (raw API dict) maps onto TurnUsage raw — Claude's
        input_tokens excludes cache, reported separately in the cache fields."""
        result = SimpleNamespace(
            usage={
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_input_tokens": 5,
                "cache_creation_input_tokens": 3,
            }
        )
        assert ClaudeSDKAdapter._usage_from_result(result) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_missing_usage_is_empty(self):
        """A ResultMessage without a usage dict yields empty usage."""
        assert (
            ClaudeSDKAdapter._usage_from_result(SimpleNamespace(usage=None))
            == TurnUsage()
        )
