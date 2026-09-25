"""Shared ClaudeSDKAdapter test constants and builders."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from band.adapters.claude_sdk import ApprovalReply, ClaudeSDKAdapter, PendingApproval
from band.runtime.decisions import DecisionRegistry
from band.runtime.tools import missing_reply_error

# The reply tool as the SDK namespaces it (MCP_TOOL_PREFIX + bare name).
SEND_MESSAGE_MCP_NAME = "mcp__band__band_send_message"
ANY_MODEL = "claude-sonnet-4-6"
# What a turn that ended without a reply going out must say; tests assert it
# by substring rather than re-deriving it.
MISSING_REPLY_TEXT = missing_reply_error("Claude SDK")


def tool_turn(mcp_tool_name: str) -> list:
    """A turn's stream in the protocol shape: the assistant calls a tool, then
    the result comes back in a user-type envelope."""
    return [
        AssistantMessage(
            content=[ToolUseBlock(id="tool-1", name=mcp_tool_name, input={})],
            model=ANY_MODEL,
        ),
        UserMessage(
            content=[
                ToolResultBlock(tool_use_id="tool-1", content="ok", is_error=False)
            ]
        ),
    ]


def error_events(mock_tools: MagicMock) -> list[str]:
    """Room-visible message of each failure reported through send_failure."""
    return [call.args[0].message for call in mock_tools.send_failure.call_args_list]


def narrated_message_types(mock_tools: MagicMock) -> list[str]:
    """``message_type`` of every event posted through send_event, in order."""
    return [
        call.kwargs["message_type"] for call in mock_tools.send_event.call_args_list
    ]


def tool_result_payload(mock_tools: MagicMock) -> dict[str, Any]:
    """The parsed content of the sole tool_result event posted through send_event."""
    [result_call] = [
        call
        for call in mock_tools.send_event.call_args_list
        if call.kwargs.get("message_type") == "tool_result"
    ]
    return json.loads(result_call.kwargs["content"])


def register_pending_approval(
    adapter: ClaudeSDKAdapter,
    room_id: str = "room-1",
    token: str = "a-1",
    *,
    tool_name: str = "Bash",
    tool_input: dict[str, Any] | None = None,
    summary: str | None = None,
    created_at: datetime | None = None,
    requester: dict[str, str] | None = None,
) -> asyncio.Future[ApprovalReply | None]:
    """Register one pending approval on adapter, returning its future."""
    future: asyncio.Future[ApprovalReply | None] = (
        asyncio.get_running_loop().create_future()
    )
    registry = adapter._pending_approvals.setdefault(
        room_id,
        DecisionRegistry(
            max_pending=adapter.max_pending_approvals_per_room,
            authorized_senders=adapter.approval_authorized_senders,
        ),
    )
    registry.register(
        PendingApproval(
            tool_name=tool_name,
            tool_input=tool_input if tool_input is not None else {},
            summary=summary or tool_name,
            created_at=created_at or datetime.now(UTC),
            future=future,
            requester=requester or {"id": "test-user", "name": "Test"},
        ),
        key=token,
    )
    return future


async def wait_for_pending_approval(
    adapter: ClaudeSDKAdapter, room_id: str = "room-1"
) -> None:
    """Yield until a real ``can_use_tool`` call has registered its approval."""
    async with asyncio.timeout(1):
        while not adapter._pending_approvals.get(room_id):
            await asyncio.sleep(0)


async def reply_to_approval(
    adapter: ClaudeSDKAdapter,
    tools: MagicMock,
    command: str,
    sender: dict[str, str],
    *,
    room_id: str = "room-1",
    tool_use_id: str | None = None,
) -> PermissionResultAllow | PermissionResultDeny:
    """Run one manual approval through a room reply; return the tool decision."""
    pending_task = asyncio.create_task(
        adapter._make_can_use_tool(room_id)(
            SEND_MESSAGE_MCP_NAME, {}, ToolPermissionContext(tool_use_id=tool_use_id)
        )
    )
    await wait_for_pending_approval(adapter, room_id)
    await adapter._handle_approval_command(
        tools=tools, room_id=room_id, command=command, args="a-1", sender=sender
    )
    return await pending_task


def result_message(
    *,
    session_id: str = "sess-xyz",
    is_error: bool = False,
    result: str | None = None,
    errors: list[str] | None = None,
    api_error_status: int | None = None,
    permission_denials: list[dict[str, Any]] | None = None,
) -> ResultMessage:
    """Build a real ``ResultMessage`` with only the fields a test cares about set."""
    return ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=100,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        result=result,
        errors=errors,
        api_error_status=api_error_status,
        permission_denials=permission_denials,
    )


def denial(tool_use_id: str, tool_name: str) -> dict[str, Any]:
    """A ``SDKPermissionDenial``-shaped entry for ``ResultMessage.permission_denials``."""
    return {"tool_name": tool_name, "tool_use_id": tool_use_id, "tool_input": {}}


def blocking_turn() -> tuple[asyncio.Event, asyncio.Event, Callable[..., Any]]:
    """A ``_process_response`` stand-in that parks a turn until released.

    Returns ``(started, release, wait_for_response)``. ``started`` fires once
    the stand-in is entered, so a test can await the detached turn actually
    reaching it before asserting against a concurrent cleanup/cancellation;
    the stand-in then blocks on ``release`` until the test sets it.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_response(*_args: Any) -> None:
        started.set()
        await release.wait()

    return started, release, wait_for_response
