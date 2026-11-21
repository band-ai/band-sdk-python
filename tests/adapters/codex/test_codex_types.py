"""Codex adapter: codex types."""

from __future__ import annotations

from band.adapters.codex import CodexAdapterConfig
from band.adapters.codex.items import (
    MESSAGE_ITEM_TYPES,
    REQUESTED_TOOL_ITEM_TYPES,
    ROUTING_ITEM_TYPES,
    SILENCE_ONLY_ITEM_TYPES,
    THOUGHT_ITEM_TYPES,
    TOOL_ITEM_TYPES,
)
from band.integrations.codex.types import (
    CodexItemType,
    CodexTokenUsage,
    build_agent_failure,
    parse_plan_steps,
)


class TestCodexTypes:
    def test_build_agent_failure_known_type(self) -> None:

        error_obj = {
            "message": "Context overflow",
            "codexErrorInfo": {
                "type": "ContextWindowExceeded",
                "code": "ctx_exceeded",
                "retryable": False,
            },
        }
        failure = build_agent_failure(error_obj, thread_id="t1", turn_id="turn-1")
        assert failure.provider == "codex"
        assert "context overflow" in failure.message.lower()
        assert failure.code == "ContextWindowExceeded"
        assert failure.detail["codex_thread_id"] == "t1"
        assert failure.detail["codex_turn_id"] == "turn-1"

    def test_build_agent_failure_unknown_type(self) -> None:

        error_obj = {
            "message": "Something weird happened",
            "codexErrorInfo": {"type": "UnknownError"},
        }
        failure = build_agent_failure(error_obj)
        assert failure.message == "Something weird happened"
        assert failure.code == "UnknownError"

    def test_parse_plan_steps(self) -> None:

        params = {
            "plan": {
                "steps": [
                    {"text": "Step 1", "status": "completed"},
                    {"text": "Step 2", "status": "inProgress"},
                    {"text": "Step 3", "status": "pending"},
                ]
            }
        }
        steps = parse_plan_steps(params)
        assert len(steps) == 3
        assert steps[0].step == "Step 1"
        assert steps[0].status == "completed"
        assert steps[2].status == "pending"

    def test_parse_plan_steps_string_entries(self) -> None:

        params = {"plan": {"steps": ["Read code", "Fix bug"]}}
        steps = parse_plan_steps(params)
        assert len(steps) == 2
        assert steps[0].step == "Read code"
        assert steps[0].status == "pending"

    def test_codex_token_usage_update(self) -> None:

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "reasoningTokens": 200,
                    "totalTokens": 1700,
                }
            }
        )
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.reasoning_tokens == 200
        assert usage.total_tokens == 1700
        meta = usage.to_metadata()
        assert meta["codex_input_tokens"] == 1000
        assert "1,700" in usage.format_summary()

    def test_codex_token_usage_update_current_schema(self) -> None:
        """The current app-server schema nests cumulative counters under
        ``tokenUsage.total`` and names reasoning ``reasoningOutputTokens``."""

        usage = CodexTokenUsage()
        usage.update(
            {
                "threadId": "t-1",
                "turnId": "turn-1",
                "tokenUsage": {
                    "total": {
                        "totalTokens": 14822,
                        "inputTokens": 14725,
                        "cachedInputTokens": 2432,
                        "outputTokens": 97,
                        "reasoningOutputTokens": 59,
                    },
                    "last": {
                        "totalTokens": 14822,
                        "inputTokens": 14725,
                        "outputTokens": 97,
                        "reasoningOutputTokens": 59,
                    },
                    "modelContextWindow": 258400,
                },
            }
        )
        assert usage.input_tokens == 14725
        assert usage.output_tokens == 97
        assert usage.reasoning_tokens == 59
        assert usage.total_tokens == 14822

    def test_config_new_flags_default_false(self) -> None:
        """All new config flags default to False."""
        config = CodexAdapterConfig()
        assert config.stream_reasoning_events is False
        assert config.stream_plan_events is False
        assert config.stream_commentary_events is False
        assert config.emit_diff_events is False
        assert config.emit_token_usage_events is False
        assert config.emit_turn_lifecycle_events is False

    def test_codex_item_type_fully_classified(self) -> None:
        """Every ``CodexItemType`` lands in exactly one of the adapter's
        buckets: tool-like, requested tool, thought-like, the skipped
        messages, routing, or silence tracking.

        A new item type added to the enum without also updating one of these
        sets currently falls through to a silent ``logger.debug`` — no room
        event, no test failure. This test is the guard: it fails loudly the
        moment the partition stops being exhaustive.
        """

        buckets = (
            TOOL_ITEM_TYPES,
            REQUESTED_TOOL_ITEM_TYPES,
            THOUGHT_ITEM_TYPES,
            MESSAGE_ITEM_TYPES,
            ROUTING_ITEM_TYPES,
            SILENCE_ONLY_ITEM_TYPES,
        )

        assert set().union(*buckets) == set(CodexItemType)
        assert sum(len(bucket) for bucket in buckets) == len(CodexItemType)

    def test_token_usage_update_handles_zero_values(self) -> None:
        """CodexTokenUsage.update() correctly handles explicit zero values."""

        usage = CodexTokenUsage()
        usage.update(
            {
                "usage": {
                    "inputTokens": 0,
                    "outputTokens": 100,
                    "reasoningTokens": 0,
                    "totalTokens": 100,
                }
            }
        )
        assert usage.input_tokens == 0
        assert usage.output_tokens == 100
        assert usage.reasoning_tokens == 0
        assert usage.total_tokens == 100
