"""Reasoning events surfaced as thought events, gated by Emit.THOUGHTS."""

from __future__ import annotations

import pytest

from band.adapters.copilot_sdk import _COPILOT_SDK_AVAILABLE
from band.core.types import AdapterFeatures, Emit
from tests.adapters.copilot_sdk.fakes import (
    FakeCopilotClient,
    ToolSchemaFakeTools,
    make_started_adapter,
    requires_copilot_sdk,
    run_message,
)

pytestmark = requires_copilot_sdk

if _COPILOT_SDK_AVAILABLE:
    from copilot.generated.session_events import AssistantReasoningData


class TestThoughts:
    @pytest.mark.asyncio
    async def test_reasoning_emitted_as_thought_events(self):
        client = FakeCopilotClient(
            turn_events=[
                AssistantReasoningData(content="pondering...", reasoning_id="r1")
            ]
        )
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.THOUGHTS})
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        thoughts = [e for e in tools.events_sent if e["message_type"] == "thought"]
        assert len(thoughts) == 1
        assert thoughts[0]["content"] == "pondering..."

    @pytest.mark.asyncio
    async def test_repeated_reasoning_id_collapses_to_one_thought(self):
        # The Copilot CLI re-emits a block's complete-text reasoning event
        # more than once per turn (same reasoning_id) — keying by id must
        # collapse the repeats so the room shows the thought once, not 2-3x.
        client = FakeCopilotClient(
            turn_events=[
                AssistantReasoningData(content="pondering...", reasoning_id="r1"),
                AssistantReasoningData(content="pondering...", reasoning_id="r1"),
                AssistantReasoningData(content="pondering...", reasoning_id="r1"),
            ]
        )
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.THOUGHTS})
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        thoughts = [e for e in tools.events_sent if e["message_type"] == "thought"]
        assert len(thoughts) == 1
        assert thoughts[0]["content"] == "pondering..."

    @pytest.mark.asyncio
    async def test_distinct_reasoning_blocks_stay_separate(self):
        # Genuinely distinct reasoning blocks (different ids) are each their
        # own thought — dedup keys on the id, not the content.
        client = FakeCopilotClient(
            turn_events=[
                AssistantReasoningData(content="first thought", reasoning_id="r1"),
                AssistantReasoningData(content="second thought", reasoning_id="r2"),
            ]
        )
        adapter = await make_started_adapter(
            client, features=AdapterFeatures(emit={Emit.THOUGHTS})
        )
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        thoughts = [e for e in tools.events_sent if e["message_type"] == "thought"]
        assert [t["content"] for t in thoughts] == ["first thought", "second thought"]

    @pytest.mark.asyncio
    async def test_reasoning_not_emitted_when_thoughts_disabled(self):
        client = FakeCopilotClient(
            turn_events=[
                AssistantReasoningData(content="pondering...", reasoning_id="r1")
            ]
        )
        adapter = await make_started_adapter(client)
        tools = ToolSchemaFakeTools()

        await run_message(adapter, tools)

        assert not [e for e in tools.events_sent if e["message_type"] == "thought"]
