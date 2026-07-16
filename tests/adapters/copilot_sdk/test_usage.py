"""Per-turn token usage capture (Emit.USAGE) for the Copilot SDK adapter.

Copilot streams usage as per-API-call ``assistant.usage`` events, so the
adapter sums them across the turn and emits one record from ``on_message``'s
finally. These tests lock that summing + the no-fold rule (reasoning_tokens is
a subset of output_tokens) and the finally contract on the failure path — the
generic emit_usage behaviors (cancellation-skip, best-effort) are base-class
and covered by the anthropic suite.
"""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE, CopilotSDKAdapter
from band.core.types import AdapterFeatures, Emit, TurnUsage
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)
from tests.adapters.usage_events import recorded_usage_payloads

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot.generated.session_events import AssistantUsageData, SessionErrorData


class TestUsage:
    def test_mapper_maps_fields_and_does_not_fold_reasoning(self):
        usage = CopilotSDKAdapter._usage_from_event(
            AssistantUsageData(
                model="m",
                input_tokens=10,
                output_tokens=5,
                reasoning_tokens=7,
                cache_read_tokens=3,
                cache_write_tokens=2,
            )
        )
        # reasoning_tokens is a SUBSET of output_tokens (rpc.py:9247), so output
        # stays 5 — folding it in would double-count to 12 (regression guard).
        assert usage == TurnUsage(
            input_tokens=10, output_tokens=5, cache_read_tokens=3, cache_write_tokens=2
        )
        assert usage.output_tokens == 5

        # Summing primitive used to aggregate a turn's per-call events.
        other = CopilotSDKAdapter._usage_from_event(
            AssistantUsageData(model="m", input_tokens=1, output_tokens=2)
        )
        assert usage + other == TurnUsage(
            input_tokens=11, output_tokens=7, cache_read_tokens=3, cache_write_tokens=2
        )

    @pytest.mark.asyncio
    async def test_usage_summed_across_turn_and_emitted_once(self):
        client = FakeCopilotClient(
            turn_events=[
                AssistantUsageData(model="m", input_tokens=10, output_tokens=5),
                AssistantUsageData(model="m", input_tokens=3, output_tokens=7),
            ]
        )
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.USAGE})
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        payloads = recorded_usage_payloads(tools)
        assert len(payloads) == 1
        assert payloads[0] == {
            "input_tokens": 13,
            "output_tokens": 12,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    @pytest.mark.asyncio
    async def test_no_usage_emitted_when_feature_disabled(self):
        client = FakeCopilotClient(
            turn_events=[
                AssistantUsageData(model="m", input_tokens=10, output_tokens=5),
                AssistantUsageData(model="m", input_tokens=3, output_tokens=7),
            ]
        )
        adapter = await make_started_adapter(client)  # default features: no USAGE
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        assert recorded_usage_payloads(tools) == []

    @pytest.mark.asyncio
    async def test_usage_emitted_even_when_turn_fails(self):
        # send_and_wait dispatches the usage event to collect, then raises on
        # the SessionErrorData — so on_message evicts/reports/re-raises and the
        # finally still emits the tokens spent before the error surfaced.
        client = FakeCopilotClient(
            turn_events=[
                AssistantUsageData(model="m", input_tokens=100, output_tokens=20),
                SessionErrorData(error_type="model_error", message="boom"),
            ]
        )
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.USAGE})
        )
        tools = ToolSchemaFakeTools()

        with pytest.raises(Exception, match="boom"):
            await run_message(adapter, tools)

        payloads = recorded_usage_payloads(tools)
        assert len(payloads) == 1
        # Full-dict assert so the failure path locks cache fields at 0 too,
        # matching test_usage_summed_across_turn_and_emitted_once.
        assert payloads[0] == {
            "input_tokens": 100,
            "output_tokens": 20,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

    @pytest.mark.asyncio
    async def test_no_usage_emitted_for_empty_turn(self):
        client = FakeCopilotClient(turn_events=[])  # no usage events this turn
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.USAGE})
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        # TurnUsage().is_empty gates emission — no false all-zero record.
        assert recorded_usage_payloads(tools) == []
