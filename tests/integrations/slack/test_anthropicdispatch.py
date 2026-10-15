"""Anthropic tool execution goes through Slack's structured dispatch."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from anthropic.types import ToolUseBlock

from band.adapters.anthropic import AnthropicAdapter
from band.core.types import PlatformMessage
from band.integrations.slack.adapter import (
    SLACK_SEND_MESSAGE_TOOL_NAME,
    SlackTeeingTools,
)
from band.integrations.slack.types import SlackRoomBinding
from band.runtime.tools import AgentTools, BandTool


def _usage() -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=3,
        output_tokens=2,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


def _message() -> PlatformMessage:
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def _bound_tools() -> tuple[SlackTeeingTools, AgentTools, AsyncMock]:
    slack = AsyncMock()
    slack.chat_postMessage = AsyncMock(return_value={"ok": True, "ts": "1.000"})
    slack.chat_update = AsyncMock(return_value={"ok": True, "ts": "1.000"})
    band = AgentTools(room_id="room-123", rest=MagicMock(), participants=[])
    tools = SlackTeeingTools(
        wrap=band,
        slack=slack,
        binding=SlackRoomBinding(app_slug="dev", channel="C", thread_ts="1.0"),
    )
    return tools, band, slack


class TestAnthropicSlackDispatch:
    @pytest.mark.asyncio
    async def test_slack_send_message_posts_and_settles_the_turn(self) -> None:
        tools, band, slack = _bound_tools()
        adapter = AnthropicAdapter(emit=())
        await adapter.on_started("TestBot", "Test bot")
        tool_use = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                ToolUseBlock(
                    type="tool_use",
                    id="tool-1",
                    name=SLACK_SEND_MESSAGE_TOOL_NAME,
                    input={"content": "hello from the room"},
                )
            ],
            usage=_usage(),
        )
        end_turn = SimpleNamespace(stop_reason="end_turn", content=[], usage=_usage())
        with patch.object(
            adapter, "_call_anthropic", AsyncMock(side_effect=[tool_use, end_turn])
        ):
            await adapter.run_judged_turn(
                msg=_message(),
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        assert slack.chat_postMessage.await_args.kwargs["text"] == "hello from the room"
        assert band.turn.replied is True

    @pytest.mark.asyncio
    async def test_band_tool_failure_renders_slack_progress(self) -> None:
        tools, _band, slack = _bound_tools()
        adapter = AnthropicAdapter(emit=())
        await adapter.on_started("TestBot", "Test bot")
        tool_use = SimpleNamespace(
            stop_reason="tool_use",
            content=[
                ToolUseBlock(
                    type="tool_use",
                    id="tool-1",
                    name=BandTool.ADD_PARTICIPANT,
                    input={},
                )
            ],
            usage=_usage(),
        )
        end_turn = SimpleNamespace(
            stop_reason="end_turn",
            content=[],
            usage=_usage(),
        )
        with patch.object(
            adapter, "_call_anthropic", AsyncMock(side_effect=[tool_use, end_turn])
        ):
            await adapter.on_message(
                _message(),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        posted = slack.chat_postMessage.await_args.kwargs
        assert posted["blocks"]
        updated = slack.chat_update.await_args.kwargs["blocks"]
        assert updated[0]["text"]["text"] == "*⚠️ Done with errors*"
