"""Codex adapter: realtime streaming."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.integrations.codex.types import CodexSessionState
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    agent_message_completed,
    agent_message_delta,
    agent_message_started,
    event_notification,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestRealtimeStreaming:
    @pytest.mark.asyncio
    async def test_reasoning_delta_streamed_as_thought(self) -> None:
        """item/reasoning/summaryTextDelta forwards as streaming thought."""
        events = [
            event_notification(
                "item/reasoning/summaryTextDelta",
                {"delta": "Analyzing the code...", "itemId": "item-1"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(stream_reasoning_events=True)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        thought_events = [
            e
            for e in tools.events_sent
            if e["message_type"] == "thought" and e["metadata"].get("streaming")
        ]
        assert len(thought_events) == 1
        assert thought_events[0]["content"] == "Analyzing the code..."
        assert thought_events[0]["metadata"]["codex_item_id"] == "item-1"

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
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        streaming_events = [
            e for e in tools.events_sent if e["metadata"].get("streaming")
        ]
        assert streaming_events == []

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
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        plan_thoughts = [
            e
            for e in tools.events_sent
            if e["message_type"] == "thought" and e["metadata"].get("subtype") == "plan"
        ]
        assert len(plan_thoughts) == 1
        assert plan_thoughts[0]["content"] == "Step 1: Read the test"

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
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        commentary_thoughts = [
            e for e in tools.events_sent if e["metadata"].get("subtype") == "commentary"
        ]
        assert len(commentary_thoughts) == 1
        assert commentary_thoughts[0]["content"] == "Let me think about this..."

        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
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
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
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
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent == []
        assert [e["content"] for e in events_of_type(tools, "thought")] == [
            "thinking...",
            "real answer",
        ]
