"""Shared ClaudeSDKAdapter test constants and builders."""

from __future__ import annotations

import asyncio
import copy
import itertools
import json
import shutil
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from claude_agent_sdk import ClaudeSDKClient
from claude_agent_sdk.types import Message

from band.adapters.claude_sdk import (
    APPROVAL_REQUESTED_TEMPLATE,
    ClaudeApprovalOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
)
from band.converters.claude_sdk import SESSION_ID_METADATA_KEY
from band.core.retention import RoomRetentionConfig
from band.core.turn import Turn
from band.core.types import (
    AgentInput,
    ApprovalMode,
    HistoryProvider,
    MessageType,
    PlatformMessage,
)
from band.integrations.claude_sdk.session_manager import (
    ClaudeRoomSession,
    ClaudeSessionManager,
)
from band.integrations.mcp.backends import BandMCPBackend
from band.runtime.tools import BAND_MCP_SERVER_NAME, MCP_TOOL_PREFIX
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools
from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.dispatch import deliver_event
from tests.baseline.decisions import ModelDecision
from tests.mcpclient import crash_backend

# The agent metadata every test adapter is started with.
AGENT_NAME = "Test Agent"
AGENT_DESCRIPTION = "An agent under test"
# The reply tool as the SDK namespaces it (MCP_TOOL_PREFIX + bare name).
SEND_MESSAGE_MCP_NAME = "mcp__band__band_send_message"
# What the runtime reports for a turn that ended without completing.
MISSING_REPLY_TEXT = MISSING_REPLY_FAILURE[1]
# A native file write, the tool call every approval path is asked about.
WRITE_NOTE = ModelDecision.call("Write", file_path="notes.md", content="todo")
# The room's approval prompt for WRITE_NOTE as the first request of a turn.
WRITE_PROMPT = APPROVAL_REQUESTED_TEMPLATE.format(
    summary="Write: notes.md", token="a-1"
)
# Short enough that a held transition reports quickly, long enough for a real
# CLI child to start and the Band MCP server to shut down on a slow runner.
REPORTING_BUDGET = RoomRetentionConfig(transition_timeout_s=1.0)
# A turn still running when its room closes is cancelled at once.
NO_SETTLE_GRACE = ClaudeSDKAdapterConfig(turn_settle_timeout_s=0)


@contextmanager
def refuse_mcp_removal(path: Path) -> Iterator[None]:
    """Refuse deletion before any private file is removed."""
    remove = shutil.rmtree

    def refuse(directory: str | Path, *args: Any, **kwargs: Any) -> None:
        if Path(directory) == path.parent:
            raise PermissionError("private MCP removal refused")
        remove(directory, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(shutil, "rmtree", refuse)
        yield


async def lease_client(
    manager: ClaudeSessionManager,
    room_id: str,
    *,
    resume_session_id: str | None = None,
) -> ClaudeSDKClient:
    """Acquire a resident client through the current retained-room contract."""
    session = manager.room(room_id)
    session.retire_outdated()
    session.adopt(resume_session_id)
    async with session.using() as client:
        return client


async def exchange(session: ClaudeRoomSession, prompt: str) -> list[Message]:
    """One leased query through the room's single reader, up to its result."""
    async with session.using() as client:
        with session.listening() as stream:
            await client.query(prompt)
            return [message async for message in stream.until_result()]


def with_approvals(
    mode: ApprovalMode = "manual", **options: Any
) -> ClaudeSDKAdapterConfig:
    """An adapter config with chat-based approvals on."""
    return ClaudeSDKAdapterConfig(approvals=ClaudeApprovalOptions(mode=mode, **options))


APPROVER = {"id": "u1", "name": "Bob", "handle": "@bob"}


def said(sender_name: str, content: str) -> dict[str, Any]:
    """One earlier room message, as raw platform history."""
    return {
        "message_type": MessageType.TEXT,
        "role": "user",
        "sender_name": sender_name,
        "content": content,
    }


def recorded_session(session_id: str) -> dict[str, Any]:
    """The task event a room persisted for a Claude session, as raw history."""
    return {
        "message_type": MessageType.TASK,
        "metadata": {SESSION_ID_METADATA_KEY: session_id},
    }


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
        # The room's shared log: every message's tools post into it.
        self.tools = FakeAgentTools(
            room_id=room_id,
            participants=[
                {**APPROVER, "role": "member", "status": "active", "type": "User"}
            ],
        )
        self._message_ids = itertools.count(1)
        self._bootstrapped = False

    def fresh_tools(self) -> FakeAgentTools:
        """The tools the runtime builds for one message: their own turn
        ledger, posting into the room's shared log (a shallow copy shares its
        lists, holds and observers; a fault set on ``self.tools`` is copied)."""
        tools = copy.copy(self.tools)
        tools.turn = Turn()
        return tools

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

    @property
    def session_band_urls(self) -> list[str]:
        """The Band MCP URL each CLI session (any room) was started with, in order."""
        return [
            session.options.mcp_servers[BAND_MCP_SERVER_NAME]["url"]
            for session in self.claude.sessions
        ]

    @property
    def session_band_ports(self) -> list[int | None]:
        """The Band MCP port each CLI session (any room) was started with, in order."""
        return [urlsplit(url).port for url in self.session_band_urls]

    async def crash_band_server(self) -> None:
        """The adapter's Band MCP server dies on its own, between turns."""
        await crash_backend(self.adapter._mcp)

    async def restart(self) -> None:
        """Start the room's adapter again, as the runtime does after a stop."""
        await self.adapter.on_started(AGENT_NAME, AGENT_DESCRIPTION)

    def beside(self, room_id: str) -> ClaudeRoom:
        """Another room served by the same adapter."""
        return ClaudeRoom(self.adapter, self.claude, room_id)

    async def leave(self) -> None:
        """The agent leaves the room; a later message bootstraps it afresh,
        even if this leave stops waiting before its cleanup finishes."""
        self._bootstrapped = False
        await self.adapter.on_cleanup(self.room_id)

    def model_call(self, tool: str, **arguments: Any) -> ModelDecision:
        """The model calling a Band-server tool, by bare name, for this room."""
        return ModelDecision.call(f"{MCP_TOOL_PREFIX}{tool}", **arguments)

    def model_reply(self, content: str) -> ModelDecision:
        """The model answering the room through the Band reply tool."""
        return ModelDecision.call(
            SEND_MESSAGE_MCP_NAME,
            content=content,
            mentions=[APPROVER["handle"]],
        )

    async def send(
        self,
        content: str,
        *,
        sender: dict[str, str] = APPROVER,
        history: tuple[dict[str, Any], ...] = (),
        session_id: str | None = None,
        tools: FakeAgentTools | None = None,
        participants_msg: str | None = None,
        contacts_msg: str | None = None,
    ) -> None:
        """Deliver one room message through the runtime's entry point, so the
        turn is judged as in production; returns once the adapter hands the
        turn back (it finished, or parked on a human).

        ``history`` is the room's raw history, ``session_id`` a Claude session
        it persisted, and ``tools`` the message's own tools (``fresh_tools()``
        by default, as the runtime builds per message). ``participants_msg``
        and ``contacts_msg`` are the runtime's pending room updates.
        """
        bootstrap, self._bootstrapped = not self._bootstrapped, True
        raw_history = [*history]
        if session_id is not None:
            raw_history.append(recorded_session(session_id))
        await deliver_event(
            self.adapter,
            AgentInput(
                msg=PlatformMessage(
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
                tools=tools or self.fresh_tools(),
                history=HistoryProvider(raw=raw_history),
                participants_msg=participants_msg,
                contacts_msg=contacts_msg,
                is_session_bootstrap=bootstrap,
                room_id=self.room_id,
            ),
        )

    def send_in_background(self, content: str) -> asyncio.Task[None]:
        """``send`` without waiting, for a delivery a held message stalls."""
        return asyncio.create_task(self.send(content))

    async def until_said(self, fragment: str, *, times: int = 1) -> None:
        """Wait until ``times`` room messages contain ``fragment``."""
        await self.tools.until_said(fragment, times=times)

    @property
    def session_manager(self) -> ClaudeSessionManager:
        manager = self.adapter._session_manager
        assert manager is not None, "the adapter was never started"
        return manager

    @property
    def mcp_backend(self) -> BandMCPBackend:
        backend = self.adapter._mcp.current
        assert backend is not None, "no Band MCP server is running"
        return backend

    @property
    def disposed(self) -> bool:
        """Whether the room's final cleanup finished and erased its session."""
        return self.session_manager.session(self.room_id) is None

    @property
    def turn_running(self) -> bool:
        """Whether the room's detached turn is still running."""
        return self.adapter._turn_running(self.room_id)

    async def settled(self) -> None:
        """Wait for a turn still running after ``send`` returned."""
        if (turn := self.adapter._turn_tasks.get(self.room_id)) is not None:
            await asyncio.wait([turn.task])


OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def band_server_replaced(room: ClaudeRoom) -> None:
    """Retire the room's CLI by crashing the Band MCP server it dials."""
    await room.crash_band_server()


async def left(room: ClaudeRoom) -> None:
    """Close the room by leaving it."""
    await room.leave()


async def shut_down(room: ClaudeRoom) -> None:
    """Close the room by shutting the whole adapter down."""
    await room.adapter.cleanup_all()


async def turn_cancelled(room: ClaudeRoom) -> None:
    """Retire the room's CLI by cancelling the turn that holds it."""
    abandoned = Hold()
    room.claude.script([abandoned, room.model_reply("never sent")])
    message = room.send_in_background("long job")
    async with abandoned:
        message.cancel()
    await asyncio.wait({message})
    if not message.cancelled():
        raise AssertionError("the abandoned message was not cancelled")
