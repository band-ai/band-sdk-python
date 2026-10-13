"""Doubles and helpers for the Cursor ACP adapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from acp.schema import NewSessionResponse
from pydantic import BaseModel, create_model

from band.adapters.cursor_acp import (
    CursorACPAdapter,
    CursorACPAdapterConfig,
    CursorTurn,
)
from band.core.protocols import AgentToolsProtocol
from band.core.types import AgentInput, HistoryProvider, PlatformMessage
from band.runtime.custom_tools import CustomToolDef
from band.runtime.tools.registry import BAND_MCP_SERVER_NAME, mcp_tool_spelling
from band.runtime.tools.types import BandTool
from band.testing import FakeAgentTools
from tests.integrations.acp.acp_toolkit.agent import (
    FakeACPAgent,
    Gate,
)
from tests.integrations.acp.acp_toolkit.harness import (
    accepted_work_finished,
    pending_decisions,
)


class DecisionTools(FakeAgentTools):
    """Room tools that enforce the real mention contract (unlike a hand-rolled
    fake, this raises on a mention-less send -- see FakeAgentTools.send_notice)
    and signal once a pending decision is visible in the room.

    Decision prompts and replies are the adapter's own posts, so they go
    through ``send_notice``.
    """

    def __init__(self) -> None:
        super().__init__()
        self.prompt_sent = asyncio.Event()

    async def send_notice(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        result = await super().send_notice(content, mentions)
        self.prompt_sent.set()
        return result

    @property
    def messages(self) -> list[str]:
        return [cast(str, sent["content"]) for sent in self.messages_sent]


class FailingDecisionTools(DecisionTools):
    def __init__(self, *, fail_after: int = 0) -> None:
        super().__init__()
        self._fail_after = fail_after

    async def send_notice(
        self, content: str, mentions: list[str] | list[dict[str, str]] | None = None
    ) -> object:
        if len(self.messages_sent) >= self._fail_after:
            raise RuntimeError("room delivery failed")
        return await super().send_notice(content, mentions)


def cursor_turn(
    adapter: CursorACPAdapter,
    room_id: str,
    tools: AgentToolsProtocol,
    requester_id: str | None,
    session_id: str | None = None,
) -> CursorTurn:
    return CursorTurn(
        room=adapter._room(room_id),
        tools=tools,
        requester_id=requester_id,
        release=asyncio.get_running_loop().create_future(),
        session_id=session_id,
    )


def room_message(
    content: str, *, room_id: str = "room-1", sender_id: str = "user-1"
) -> PlatformMessage:
    return PlatformMessage(
        id=str(uuid4()),
        room_id=room_id,
        content=content,
        sender_id=sender_id,
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def said(tools: FakeAgentTools) -> list[str]:
    return [cast(str, sent["content"]) for sent in tools.messages_sent]


def permission_requests(tools: FakeAgentTools) -> list[str]:
    return [message for message in said(tools) if "needs permission" in message]


class CursorRoom:
    """A Cursor room whose ``agent acp`` peer is a scripted in-process ACP
    agent: each message goes through ``on_event`` with its own tools, as the
    runtime delivers it, so every turn is judged."""

    def __init__(self, adapter: CursorACPAdapter, agent: FakeACPAgent) -> None:
        self.adapter = adapter
        self.agent = agent
        self._bootstrapped = False

    async def send(self, content: str) -> FakeAgentTools:
        """Deliver ``content``; return the tools its turn posted through."""
        tools = FakeAgentTools(room_id="room-1")
        bootstrap, self._bootstrapped = not self._bootstrapped, True
        await self.adapter.on_event(
            AgentInput(
                msg=room_message(content),
                tools=tools,
                history=HistoryProvider(raw=[]),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=bootstrap,
                room_id="room-1",
            )
        )
        return tools

    async def turns_finished(self) -> None:
        """Wait for every turn still running after its message returned; a
        disposed room already joined its turns."""
        if (room := self.adapter._rooms.get("room-1")) is not None:
            await accepted_work_finished(room)


def pending_tokens(adapter: CursorACPAdapter) -> list[str]:
    """Every open decision token, across the adapter's rooms."""
    return [token for room in pending_decisions(adapter).values() for token in room]


def activate(adapter: CursorACPAdapter, turn: CursorTurn) -> CursorTurn:
    """Accept ``turn`` and let it take its room, as a running turn does."""
    adapter._accept(turn)
    adapter._take_room(turn)
    return turn


async def resident(open_room: Callable[..., Awaitable[CursorRoom]]) -> CursorACPAdapter:
    """A Cursor adapter whose ``room-1`` has a live residency, so a turn
    activated there speaks for it."""
    room = await open_room(
        FakeACPAgent().will_call_mcp_tool(
            "reply",
            BandTool.SEND_MESSAGE,
            arguments={"content": "ready", "mentions": ["@user-1"]},
        )
    )
    await room.send("start")
    await room.turns_finished()
    return room.adapter


def cursor_in(
    workspace_root: Path, config: CursorACPAdapterConfig | None = None
) -> CursorACPAdapter:
    """A Cursor adapter whose room workspaces live under ``workspace_root``."""
    return CursorACPAdapter(
        config, workspace_for_room=lambda room_id: str(workspace_root / room_id)
    )


class EchoInput(BaseModel):
    text: str


def echo(arguments: EchoInput) -> str:
    return arguments.text


def custom_reply() -> str:
    return "custom reply"


def cursor_custom_tools(
    echo_handler: Callable[[EchoInput], str],
) -> list[CustomToolDef]:
    return [
        (EchoInput, echo_handler),
        (
            create_model(
                f"{mcp_tool_spelling(BAND_MCP_SERVER_NAME, BandTool.SEND_MESSAGE)}Input"
            ),
            custom_reply,
        ),
    ]


class SetupGatedAgent(FakeACPAgent):
    def __init__(self) -> None:
        super().__init__()
        self.setup = Gate()

    async def new_session(
        self, cwd: str, mcp_servers: Any = None, **kwargs: Any
    ) -> NewSessionResponse:
        self.setup.received.set()
        await self.setup.release.wait()
        return await super().new_session(cwd, mcp_servers, **kwargs)
