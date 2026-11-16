"""Codex adapter: realtime streaming."""

from __future__ import annotations

from typing import Any

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.types import MessageType
from band.integrations.codex.types import CodexMetadataKey, CodexNotificationMethod
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FIRST_THREAD_ID,
    FakeCodexClient,
    agent_message_completed,
    agent_message_delta,
    agent_message_started,
    event_notification,
    make_codex_adapter,
    turn_completed,
)

# The room, thread and turn every streamed delta of the first turn names.
FIRST_TURN = {
    CodexMetadataKey.ROOM: ROOM_ID,
    CodexMetadataKey.THREAD: FIRST_THREAD_ID,
    CodexMetadataKey.TURN: "turn-1",
}


def streamed(tools: ToolSchemaFakeTools) -> list[dict[str, Any]]:
    """The streamed thought events, in order."""
    return [
        event
        for event in events_of_type(tools, MessageType.THOUGHT)
        if event["metadata"].get("streaming")
    ]


class TestRealtimeStreaming:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "method",
        [
            CodexNotificationMethod.REASONING_SUMMARY_DELTA,
            CodexNotificationMethod.REASONING_TEXT_DELTA,
        ],
    )
    async def test_reasoning_delta_streamed_as_thought(
        self, method: CodexNotificationMethod
    ) -> None:
        """Both reasoning delta kinds forward as streaming thoughts."""
        events = [
            event_notification(
                method, {"delta": "Analyzing the code...", "itemId": "item-1"}
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_reasoning_events=True)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert [(e["content"], e["metadata"]) for e in streamed(tools)] == [
            (
                "Analyzing the code...",
                {
                    "streaming": True,
                    CodexMetadataKey.EVENT_TYPE: method,
                    CodexMetadataKey.ITEM_ID: "item-1",
                    **FIRST_TURN,
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_reasoning_delta_ignored_when_disabled(self) -> None:
        """Reasoning deltas are skipped when stream_reasoning_events=False."""
        events = [
            event_notification(
                "item/reasoning/summaryTextDelta",
                {"delta": "Thinking...", "itemId": "item-1"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_reasoning_events=False)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert streamed(tools) == []

    @pytest.mark.asyncio
    async def test_plan_delta_streamed_as_thought(self) -> None:
        """item/plan/delta forwards as streaming thought with plan subtype."""
        events = [
            event_notification(
                "item/plan/delta",
                {"delta": "Step 1: Read the test", "itemId": "plan-1"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_plan_events=True)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert [(e["content"], e["metadata"]) for e in streamed(tools)] == [
            (
                "Step 1: Read the test",
                {
                    "streaming": True,
                    "subtype": "plan",
                    CodexMetadataKey.ITEM_ID: "plan-1",
                    **FIRST_TURN,
                },
            )
        ]

    @pytest.mark.asyncio
    async def test_commentary_phase_streamed_as_thought(self) -> None:
        """A started commentary item supplies the phase for streamed deltas."""
        events = [
            agent_message_started("comment", phase="commentary"),
            agent_message_delta("Let me think about this...", "comment"),
            agent_message_completed(
                "Let me think about this...", "comment", phase="commentary"
            ),
            agent_message_started("answer", phase="final_answer"),
            agent_message_delta("Here is the answer.", "answer"),
            agent_message_completed(
                "Here is the answer.", "answer", phase="final_answer"
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_commentary_events=True)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert [(e["content"], e["metadata"]) for e in streamed(tools)] == [
            (
                "Let me think about this...",
                {
                    "streaming": True,
                    "subtype": "commentary",
                    CodexMetadataKey.ITEM_ID: "comment",
                    **FIRST_TURN,
                },
            )
        ]

        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, MessageType.THOUGHT)] == [
            "Let me think about this...",
            "Here is the answer.",
        ]

    @pytest.mark.asyncio
    async def test_streamed_commentary_is_not_replayed_on_completion(
        self,
    ) -> None:
        """Streamed commentary is not replayed when its item completes."""
        events = [
            agent_message_started("comment", phase="commentary"),
            agent_message_delta("thinking...", "comment"),
            agent_message_completed("thinking...", "comment", phase="commentary"),
            agent_message_started("answer", phase="final_answer"),
            agent_message_delta("real answer", "answer"),
            agent_message_completed("real answer", "answer", phase="final_answer"),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_commentary_events=True)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, MessageType.THOUGHT)] == [
            "thinking...",
            "real answer",
        ]

    @pytest.mark.asyncio
    async def test_unstreamed_completed_messages_are_separate_thoughts(
        self,
    ) -> None:
        """Unstreamed completed messages remain separate thoughts."""
        events = [
            agent_message_started("comment", phase="commentary"),
            agent_message_delta("thinking...", "comment"),
            agent_message_completed("thinking...", "comment", phase="commentary"),
            agent_message_started("answer", phase="final_answer"),
            agent_message_delta("real answer", "answer"),
            agent_message_completed("real answer", "answer", phase="final_answer"),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_commentary_events=False)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, MessageType.THOUGHT)] == [
            "thinking...",
            "real answer",
        ]
