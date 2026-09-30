"""Shared ClaudeSDKAdapter test constants and builders."""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, datetime
from typing import Any

from band.adapters.claude_sdk import (
    ClaudeSDKAdapter,
)
from band.converters.claude_sdk import (
    SESSION_ID_METADATA_KEY,
    ClaudeSDKSessionState,
)
from band.core.types import MessageType, PlatformMessage
from band.runtime.tools import MCP_TOOL_PREFIX, missing_reply_error
from band.testing import FakeAgentTools
from tests.adapters.claude_sdk.fakecli import FakeClaude
from tests.baseline.decisions import ModelDecision

# The reply tool as the SDK namespaces it (MCP_TOOL_PREFIX + bare name).
SEND_MESSAGE_MCP_NAME = "mcp__band__band_send_message"
# What a turn that ended without a reply going out must say; tests assert it
# by substring rather than re-deriving it.
MISSING_REPLY_TEXT = missing_reply_error("Claude SDK")


APPROVER = {"id": "u1", "name": "Bob", "handle": "@bob"}


class ClaudeRoom:
    """A Claude SDK room driven end to end: people post messages, the scripted
    CLI answers them through the real SDK client, and the test reads what the
    room saw."""

    def __init__(
        self, adapter: ClaudeSDKAdapter, claude: FakeClaude, room_id: str = "room-1"
    ) -> None:
        self.adapter = adapter
        self.claude = claude
        self.room_id = room_id
        self.tools = self.fresh_tools()
        self._message_ids = itertools.count(1)
        self._bootstrapped = False

    def fresh_tools(self) -> FakeAgentTools:
        """A new view of this room, like the tools the runtime builds per message."""
        return FakeAgentTools(
            room_id=self.room_id,
            participants=[
                {**APPROVER, "role": "member", "status": "active", "type": "User"}
            ],
        )

    @property
    def chat(self) -> list[str]:
        return [message["content"] for message in self.tools.messages_sent]

    @property
    def events(self) -> list[str]:
        return [event["message_type"] for event in self.tools.events_sent]

    @property
    def failures(self) -> list[str]:
        return [
            event["content"]
            for event in self.tools.events_sent
            if event["message_type"] == MessageType.ERROR
        ]

    @property
    def tool_call_names(self) -> list[str]:
        """The tool name each narrated tool_call event carries, in order."""
        return [
            json.loads(event["content"])["name"]
            for event in self.tools.events_sent
            if event["message_type"] == MessageType.TOOL_CALL
        ]

    @property
    def tool_outputs(self) -> dict[str, Any]:
        """Each narrated tool result's output, by the tool's bare name."""
        results = [
            json.loads(event["content"])
            for event in self.tools.events_sent
            if event["message_type"] == MessageType.TOOL_RESULT
        ]
        return {result["name"]: result["output"] for result in results}

    @property
    def tool_errors(self) -> dict[str, bool | None]:
        """Each narrated tool result's ``is_error``, by the tool's bare name."""
        return {
            json.loads(event["content"])["name"]: json.loads(event["content"])[
                "is_error"
            ]
            for event in self.tools.events_sent
            if event["message_type"] == MessageType.TOOL_RESULT
        }

    @property
    def reported_failures(self) -> list[dict[str, Any]]:
        """The structured ``AgentFailure`` behind each error event."""
        return [
            event["metadata"]["failure"]
            for event in self.tools.events_sent
            if event["message_type"] == MessageType.ERROR
        ]

    @property
    def persisted_sessions(self) -> list[str]:
        """Session ids the room recorded for a later resume, in order."""
        return [
            event["metadata"][SESSION_ID_METADATA_KEY]
            for event in self.tools.events_sent
            if event["message_type"] == MessageType.TASK
            and SESSION_ID_METADATA_KEY in (event["metadata"] or {})
        ]

    def beside(self, room_id: str) -> ClaudeRoom:
        """Another room served by the same adapter."""
        return ClaudeRoom(self.adapter, self.claude, room_id)

    async def leave(self) -> None:
        """The agent leaves the room; a later message bootstraps it afresh."""
        await self.adapter.on_cleanup(self.room_id)
        self._bootstrapped = False

    def model_call(self, tool: str, **arguments: Any) -> ModelDecision:
        """The model calling a Band-server tool, by bare name, for this room."""
        return ModelDecision.call(
            f"{MCP_TOOL_PREFIX}{tool}", chat_id=self.room_id, **arguments
        )

    def model_reply(self, content: str) -> ModelDecision:
        """The model answering the room through the Band reply tool."""
        return ModelDecision.call(
            SEND_MESSAGE_MCP_NAME,
            chat_id=self.room_id,
            content=content,
            mentions=[APPROVER["handle"]],
        )

    async def send(
        self,
        content: str,
        *,
        sender: dict[str, str] = APPROVER,
        history: str = "",
        session_id: str | None = None,
        tools: FakeAgentTools | None = None,
    ) -> None:
        """Deliver one room message; returns once the adapter hands the turn
        back (it finished, or parked on a human).

        ``tools`` stands in for the fresh tools the runtime builds per message.
        """
        bootstrap, self._bootstrapped = not self._bootstrapped, True
        await self.adapter.on_message(
            PlatformMessage(
                id=f"msg-{next(self._message_ids)}",
                room_id=self.room_id,
                content=content,
                sender_id=sender["id"],
                sender_type="User",
                sender_name=sender["name"],
                message_type="text",
                metadata={},
                created_at=datetime.now(UTC),
            ),
            tools or self.tools,
            ClaudeSDKSessionState(text=history, session_id=session_id),
            None,
            None,
            is_session_bootstrap=bootstrap,
            room_id=self.room_id,
        )

    def send_in_background(self, content: str) -> asyncio.Task[None]:
        """``send`` without waiting, for a delivery a held message stalls."""
        return asyncio.create_task(self.send(content))

    async def until_said(self, fragment: str, *, times: int = 1) -> None:
        """Wait until ``times`` room messages contain ``fragment``."""
        await self.tools.until_said(fragment, times=times)

    async def settled(self) -> None:
        """Wait for a turn still running after ``send`` returned."""
        if (turn := self.adapter._turn_tasks.get(self.room_id)) is not None:
            await asyncio.wait([turn])
