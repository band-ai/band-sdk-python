"""Agent compositor: StartupRaceCondition."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.agent import Agent
from band.client.streaming import (
    MessageCreatedPayload,
    MessageMetadata,
)
from band.core.simple_adapter import SimpleAdapter
from band.platform.event import MessageEvent
from band.runtime.types import ConversationContext
from band.testing.platform import platform_connection_stub


class TestStartupRaceCondition:
    """Tests for startup sequence timing.

    Verifies that adapter.on_started() is called BEFORE any message processing.
    This prevents a race condition where messages arrive before the system prompt is set.
    """

    @pytest.mark.asyncio
    async def test_adapter_on_started_before_first_message(self):
        """System prompt must be set before any message processing."""

        # Track the order of calls
        call_order = []

        class TrackingAdapter(SimpleAdapter):
            def __init__(self):
                super().__init__(history_converter=MagicMock())
                self._system_prompt = ""

            async def on_started(self, agent_name: str, agent_description: str) -> None:
                call_order.append("on_started")
                self._system_prompt = f"You are {agent_name}"

            async def on_message(
                self,
                msg,
                tools,
                history,
                participants_msg,
                contacts_msg,
                *,
                is_session_bootstrap: bool,
                room_id: str,
            ) -> None:
                call_order.append("on_message")
                # This MUST NOT be empty if on_started was called first
                assert self._system_prompt != "", (
                    "System prompt was empty during message processing! "
                    "adapter.on_started() was not called before message arrived."
                )
                await tools.no_reply()

            async def on_cleanup(self, room_id: str) -> None:
                pass

        adapter = TrackingAdapter()

        # Create a runtime that simulates immediate message delivery during start()
        class ImmediateMessageRuntime:
            agent_name = "TestBot"
            agent_description = "A test bot"
            agent_id = "agent-123"
            connection = platform_connection_stub(agent_id="agent-123")
            feature_flags = None
            _on_execute = None

            async def initialize(self) -> None:
                """Initialize without starting message processing."""

            def claim_single_instance(self) -> None:
                pass

            def release_single_instance(self) -> None:
                pass

            async def start(
                self,
                on_execute,
                on_cleanup=None,
                on_control=None,
                on_message_control=None,
            ):
                self._on_execute = on_execute

                # Create a proper MessageEvent that passes through preprocessor
                mock_ctx = MagicMock()
                mock_ctx.is_llm_initialized = False
                mock_ctx.mark_llm_initialized = MagicMock()
                mock_ctx.get_context = AsyncMock(
                    return_value=ConversationContext(
                        room_id="room-123",
                        messages=[],
                        participants=[],
                        hydrated_at=datetime.now(UTC),
                    )
                )
                mock_ctx.participants = []
                mock_ctx.participants_changed = MagicMock(return_value=False)
                mock_ctx.room_id = "room-123"

                # Create a proper MessageEvent (not a MagicMock)
                message_event = MessageEvent(
                    room_id="room-123",
                    payload=MessageCreatedPayload(
                        id="msg-1",
                        content="Hello",
                        sender_id="user-1",
                        sender_type="User",
                        message_type="text",
                        metadata=MessageMetadata(mentions=[], status="sent"),
                        chat_room_id="room-123",
                        inserted_at=datetime.now(UTC).isoformat(),
                        updated_at=datetime.now(UTC).isoformat(),
                    ),
                )

                # This simulates what happens when a message arrives immediately
                await on_execute(mock_ctx, message_event)

            async def stop(self):
                pass

            async def run_forever(self, *, install_signal_handlers: bool = True):
                pass

        runtime = ImmediateMessageRuntime()
        agent = Agent(runtime=runtime, adapter=adapter)

        # This will fail if on_message is called before on_started
        await agent.start()

        # Verify on_started was called first
        assert len(call_order) >= 1, "No calls were made"
        assert call_order[0] == "on_started", (
            f"Expected on_started to be called first, but got: {call_order}"
        )
