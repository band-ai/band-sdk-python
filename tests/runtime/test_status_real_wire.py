"""``PlatformRuntime.status()`` over a real (fake-server-backed) wire.

The connection, room join, and room leave go through the real
BandLink/WebSocketClient/PHXChannelsClient stack and RoomPresence; only REST
is stubbed, as in the other real-wire suites.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from band_rest import AgentMe, GetAgentMeResponse

from band.platform.link import BandLink
from band.runtime.execution import ExecutionState
from band.runtime.platform_runtime import PlatformRuntime
from band.runtime.status import AgentStatus, RoomStatus
from band.testing import fake_phoenix_server
from tests.runtime.conftest import chat_row, platform_msg, wait_for_condition

_ROOM_EVENT = {
    "id": "room-1",
    "inserted_at": "2026-09-26T00:00:00Z",
    "updated_at": "2026-09-26T00:00:00Z",
}


def _stub_rest(link: BandLink, *, next_messages: list[Any]) -> None:
    """REST behind a real WebSocket: identity, one listed room, one message."""
    link.rest = MagicMock()
    link.rest.agent_api_identity.get_agent_me = AsyncMock(
        return_value=GetAgentMeResponse(
            data=AgentMe(
                handle="test/bot",
                id="agent-123",
                inserted_at=datetime.now(UTC),
                name="Bot",
                description="d",
                owner_uuid="owner-1",
                updated_at=datetime.now(UTC),
                feature_flags={},
            )
        )
    )
    link.rest.agent_api_chats.list_agent_chats = AsyncMock(
        return_value=MagicMock(
            data=[chat_row("room-1")], metadata=MagicMock(total_pages=1)
        )
    )
    link.rest.agent_api_participants.list_agent_chat_participants = AsyncMock(
        return_value=MagicMock(data=[])
    )
    link.rest.agent_api_context.get_agent_chat_context = AsyncMock(
        return_value=MagicMock(
            data=[], metadata=MagicMock(has_more=False, next_cursor=None)
        )
    )
    pending = list(next_messages)

    async def next_message(*_args: Any, **_kwargs: Any) -> Any:
        return pending.pop(0) if pending else None

    link.get_next_message = AsyncMock(side_effect=next_message)
    link.mark_processing = AsyncMock()
    link.mark_processed = AsyncMock()
    link.mark_failed = AsyncMock()


def _room_states(status: AgentStatus) -> list[tuple[str, ExecutionState | None]]:
    return [(room.room_id, room.state) for room in status.rooms]


@pytest.mark.timeout(20)
async def test_status_follows_the_agent_through_its_lifecycle() -> None:
    async with fake_phoenix_server() as server:
        runtime = PlatformRuntime(
            agent_id="agent-123", api_key="k", ws_url=server.url, rest_url="x"
        )
        link = BandLink(agent_id="agent-123", api_key="k", ws_url=server.url)
        _stub_rest(link, next_messages=[platform_msg("msg-1")])
        runtime._link = link

        before_start = runtime.status()
        turn_started = asyncio.Event()
        release_turn = asyncio.Event()

        async def on_execute(_ctx: Any, _event: Any) -> None:
            turn_started.set()
            await release_turn.wait()

        await runtime.start(on_execute=on_execute)
        try:
            await asyncio.wait_for(turn_started.wait(), timeout=5.0)
            processing = runtime.status()

            release_turn.set()
            await wait_for_condition(
                lambda: (
                    _room_states(runtime.status()) == [("room-1", ExecutionState.IDLE)]
                ),
                timeout=5.0,
            )
            idle = runtime.status()

            await server.push("agent_rooms:agent-123", "room_removed", _ROOM_EVENT)
            await wait_for_condition(lambda: runtime.status().rooms == (), timeout=5.0)
            left = runtime.status()
        finally:
            await runtime.stop()
        stopped = runtime.status()

    assert before_start == AgentStatus(
        connected=False, last_disconnect_reason=None, started_at=None, rooms=()
    )
    assert processing.connected and processing.started_at is not None
    assert _room_states(processing) == [("room-1", ExecutionState.PROCESSING)]
    assert (idle.connected, left.connected, left.rooms) == (True, True, ())
    assert (stopped.connected, stopped.started_at, stopped.rooms) == (False, None, ())
    # A snapshot taken mid-turn is not rewritten by everything that followed.
    assert processing.rooms == (
        RoomStatus(room_id="room-1", state=ExecutionState.PROCESSING),
    )


@pytest.mark.timeout(20)
async def test_restart_reports_a_fresh_start_and_supersede_reason() -> None:
    async with fake_phoenix_server() as server:
        runtime = PlatformRuntime(
            agent_id="agent-123", api_key="k", ws_url=server.url, rest_url="x"
        )
        link = BandLink(agent_id="agent-123", api_key="k", ws_url=server.url)
        _stub_rest(link, next_messages=[])
        runtime._link = link

        await runtime.start(on_execute=AsyncMock())
        first = runtime.status()
        await server.push(
            "agent_control:agent-123",
            "supersede",
            {
                "reason": "session.already_connected",
                "message": "superseded",
                "retryable": False,
                "target_socket_id": "agent_socket:agent-123",
                "correlation_id": "evict-1",
            },
        )
        await wait_for_condition(
            lambda: runtime.status().last_disconnect_reason is not None, timeout=5.0
        )
        superseded = runtime.status()
        await runtime.stop()

        await runtime.start(on_execute=AsyncMock())
        try:
            restarted = runtime.status()
        finally:
            await runtime.stop()

    assert superseded.connected is False
    assert superseded.last_disconnect_reason is not None
    assert superseded.last_disconnect_reason.reason == "session.already_connected"
    assert restarted.connected is True
    assert restarted.last_disconnect_reason is None
    assert first.started_at is not None and restarted.started_at is not None
    assert restarted.started_at > first.started_at
