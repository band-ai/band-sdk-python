"""Unit tests for the Emit.USAGE per-adapter usage-mapping helpers.

Each adapter maps its framework's response/usage object onto the shared
``TurnUsage``. These are pure static methods, so they're tested directly with
lightweight stand-ins (SimpleNamespace / dict) — no live LLM, no full adapter
construction. The emission path itself (gating on Emit.USAGE, empty-skip,
best-effort) lives on ``SimpleAdapter.emit_usage`` and is covered in
``test_anthropic_adapter.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from band.adapters.agno import AgnoAdapter
from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.google_adk import GoogleADKAdapter
from band.core.types import TurnUsage


class TestClaudeSDKUsageMapping:
    def test_maps_usage_dict(self):
        """ResultMessage.usage (raw API dict) maps onto TurnUsage."""
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


class TestAgnoUsageMapping:
    def test_maps_run_metrics(self):
        """RunOutput.metrics (aggregated) maps onto TurnUsage; names line up."""
        metrics = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )
        response = SimpleNamespace(metrics=metrics)
        assert AgnoAdapter._usage_from_response(response) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_missing_metrics_is_empty(self):
        """A RunOutput without metrics yields empty usage."""
        assert (
            AgnoAdapter._usage_from_response(SimpleNamespace(metrics=None))
            == TurnUsage()
        )


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
