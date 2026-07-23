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

import pytest

from band.adapters.agno import AgnoAdapter
from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.adapters.codex import CodexAdapter
from band.adapters.gemini import GeminiAdapter
from band.adapters.google_adk import GoogleADKAdapter
from band.adapters.letta import LettaAdapter
from band.core.types import TurnUsage
from band.integrations.opencode import OpencodeMessageInfo


class TestTurnUsageConstructors:
    """The shared from_object / from_mapping constructors the adapters build on."""

    def test_from_object_reads_named_attrs(self):
        src = SimpleNamespace(inp=100, out=20, cr=5, cw=3)
        assert TurnUsage.from_object(
            src, input="inp", output="out", cache_read="cr", cache_write="cw"
        ) == TurnUsage(100, 20, 5, 3)

    def test_from_object_none_source_is_empty(self):
        assert TurnUsage.from_object(None, input="i", output="o") == TurnUsage()

    def test_from_object_omitted_cache_keys_stay_zero(self):
        src = SimpleNamespace(i=100, o=20, cache_read_tokens=999)
        # cache_read/cache_write not passed → not read even if present on src
        assert TurnUsage.from_object(src, input="i", output="o") == TurnUsage(100, 20)

    def test_from_mapping_reads_named_keys(self):
        data = {"i": 100, "o": 20, "cr": 5, "cw": 3}
        assert TurnUsage.from_mapping(
            data, input="i", output="o", cache_read="cr", cache_write="cw"
        ) == TurnUsage(100, 20, 5, 3)

    @pytest.mark.parametrize("bad", [None, "not a mapping", 42, ["list"]])
    def test_from_mapping_non_mapping_is_empty(self, bad):
        assert TurnUsage.from_mapping(bad, input="i", output="o") == TurnUsage()

    def test_missing_and_non_int_fields_default_to_zero(self):
        data = {"i": "oops", "o": None}  # non-int / missing
        assert TurnUsage.from_mapping(data, input="i", output="o") == TurnUsage()

    def test_fields_are_raw_no_cache_folding(self):
        """Per the convention, values pass through raw — cache is never folded
        into input regardless of provider."""
        data = {"i": 100, "o": 20, "cr": 5, "cw": 3}
        assert TurnUsage.from_mapping(
            data, input="i", output="o", cache_read="cr", cache_write="cw"
        ) == TurnUsage(
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )


class TestIsUsageEvent:
    """The shared discriminator task-event consumers use to skip usage records."""

    def test_true_when_band_usage_present(self):
        from band.core.types import USAGE_METADATA_KEY, is_usage_event

        assert is_usage_event({USAGE_METADATA_KEY: {"input_tokens": 1}}) is True

    def test_false_for_lifecycle_task_or_non_mapping(self):
        from band.core.types import is_usage_event

        assert is_usage_event({"codex_thread_id": "x"}) is False
        assert is_usage_event(None) is False
        assert is_usage_event("nope") is False


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


class TestOpencodeUsageMapping:
    def test_maps_info_tokens_with_nested_cache(self):
        """OpenCode assistant info.tokens (nested cache) maps onto TurnUsage.

        OpenCode reports reasoning disjointly from output (its own total is
        input + output + reasoning + cache), so reasoning folds into
        output_tokens: 20 + 7 = 27.
        """
        info = OpencodeMessageInfo.model_validate(
            {
                "tokens": {
                    "input": 100,
                    "output": 20,
                    "reasoning": 7,
                    "cache": {"read": 5, "write": 3},
                }
            }
        )
        assert info.tokens is not None
        assert info.tokens.to_turn_usage() == TurnUsage(
            input_tokens=100,
            output_tokens=27,
            cache_read_tokens=5,
            cache_write_tokens=3,
        )

    def test_missing_or_malformed_tokens_is_empty(self):
        """No tokens (or a non-dict tokens) parses as absent usage."""
        assert OpencodeMessageInfo.model_validate({}).tokens is None
        assert OpencodeMessageInfo.model_validate({"tokens": "nope"}).tokens is None


class TestCodexUsageMapping:
    def test_maps_per_turn_deltas(self):
        """Codex's per-turn token deltas (not thread cumulatives) map to TurnUsage.

        Codex reports reasoning disjointly from output (its own total is
        input + output + reasoning), so reasoning folds into output_tokens:
        42 + 8 = 50.
        """
        usage = SimpleNamespace(
            turn_input_tokens=130, turn_output_tokens=42, turn_reasoning_tokens=8
        )
        assert CodexAdapter._turn_usage(usage) == TurnUsage(
            input_tokens=130, output_tokens=50
        )

    def test_none_usage_is_empty(self):
        """No usage object for the thread yields empty usage (no emit)."""
        assert CodexAdapter._turn_usage(None) == TurnUsage()
