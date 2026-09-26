"""Idle resource release for ClaudeSDKAdapter: stop the room's process, resume later."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from band.converters.claude_sdk import ClaudeSDKSessionState
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools

ROOM = "room-1"
EARLIER_FACT = "My favorite color is teal."


class FakeSessionManager:
    """Tracks one Claude client per room the way ClaudeSessionManager does."""

    def __init__(self, *, resume_error: Exception | None = None) -> None:
        self.sessions: dict[str, MagicMock] = {}
        self.created_with: list[str | None] = []
        self.queries: list[str] = []
        self.resume_error = resume_error
        self.cleanup_entered = asyncio.Event()
        self.cleanup_gate: asyncio.Event | None = None

    async def start(self) -> None: ...

    async def stop(self) -> None:
        self.sessions.clear()

    def has_session(self, room_id: str) -> bool:
        return room_id in self.sessions

    async def get_or_create_session(
        self, room_id: str, resume_session_id: str | None = None
    ) -> MagicMock:
        if room_id in self.sessions:
            return self.sessions[room_id]
        self.created_with.append(resume_session_id)
        if resume_session_id and self.resume_error is not None:
            raise self.resume_error
        client = MagicMock()
        client.query = AsyncMock(side_effect=self.queries.append)
        self.sessions[room_id] = client
        return client

    async def cleanup_session(self, room_id: str) -> None:
        self.cleanup_entered.set()
        if self.cleanup_gate is not None:
            await self.cleanup_gate.wait()
        self.sessions.pop(room_id, None)


def _message(msg_id: str, content: str) -> PlatformMessage:
    return PlatformMessage(
        id=msg_id,
        room_id=ROOM,
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


def _transcript_item(msg_id: str, content: str) -> dict[str, Any]:
    return {
        "id": msg_id,
        "content": content,
        "sender_id": "user-1",
        "sender_type": "User",
        "sender_name": "Alice",
        "message_type": "text",
    }


class Room:
    def __init__(self, adapter: ClaudeSDKAdapter, manager: FakeSessionManager) -> None:
        self.adapter = adapter
        self.manager = manager
        self.tools = FakeAgentTools()

    async def turn(self, msg_id: str, content: str, *, bootstrap: bool) -> None:
        await self.adapter.on_message(
            _message(msg_id, content),
            self.tools,
            ClaudeSDKSessionState(text=""),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=bootstrap,
            room_id=ROOM,
        )

    @property
    def last_query(self) -> str:
        return self.manager.queries[-1]


async def claude_room(workspace: str, **manager_kwargs: Any) -> Room:
    """A started Claude adapter whose room has finished one bootstrap turn."""
    manager = FakeSessionManager(**manager_kwargs)
    adapter = ClaudeSDKAdapter(workspace_for_room=lambda _room_id: workspace)

    async def complete_turn(_client: Any, room_id: str, _tools: Any) -> None:
        adapter._session_ids[room_id] = "sess-1"

    with patch("band.adapters.claude_sdk.ClaudeSessionManager", return_value=manager):
        await adapter.on_started("Claude", "A coding agent")
    adapter._process_response = complete_turn  # type: ignore[method-assign]
    built = Room(adapter, manager)
    await built.turn("msg-0", EARLIER_FACT, bootstrap=True)
    return built


@pytest.fixture
async def room(tmp_path):
    rooms: list[Room] = []

    async def build(**manager_kwargs: Any) -> Room:
        built = await claude_room(str(tmp_path), **manager_kwargs)
        rooms.append(built)
        return built

    yield build
    for built in rooms:
        await built.adapter.cleanup_all()


async def test_a_released_room_resumes_its_session_in_the_same_workspace(room) -> None:
    r = await room()

    await r.adapter.release_room_resources(ROOM)

    assert (r.manager.has_session(ROOM), ROOM in r.adapter._room_workspaces) == (
        False,
        True,
    )
    await r.turn("msg-1", "What is my favorite color?", bootstrap=False)
    assert r.manager.created_with == [None, "sess-1"]
    assert "Your memory of this room" not in r.last_query
    assert r.adapter._released_sessions == {}


async def test_failed_resume_after_release_replays_the_fetched_room_history(
    room,
) -> None:
    r = await room(resume_error=RuntimeError("No conversation found"))
    await r.adapter.release_room_resources(ROOM)
    r.tools.set_room_context(
        [
            _transcript_item("msg-0", EARLIER_FACT),
            _transcript_item("msg-1", "What is my favorite color?"),
        ]
    )

    await r.turn("msg-1", "What is my favorite color?", bootstrap=False)

    assert r.manager.created_with == [None, "sess-1", None]
    memory, live = r.last_query.split("\n\n")
    assert (EARLIER_FACT in memory, "What is my favorite color?" in live) == (
        True,
        True,
    )
    assert "What is my favorite color?" not in memory


async def test_failed_resume_without_history_fails_the_turn_and_keeps_the_session(
    room,
) -> None:
    r = await room(resume_error=RuntimeError("No conversation found"))
    await r.adapter.release_room_resources(ROOM)
    r.tools.fetch_room_context = AsyncMock(side_effect=OSError("platform down"))

    with pytest.raises(OSError):
        await r.turn("msg-1", "What is my favorite color?", bootstrap=False)

    assert r.manager.created_with == [None, "sess-1"]
    assert r.adapter._released_sessions == {ROOM: "sess-1"}


async def test_a_room_mid_turn_is_not_released(room) -> None:
    r = await room()
    gate = asyncio.Event()
    turn_started = asyncio.Event()

    async def parked_turn(*_args: Any) -> None:
        turn_started.set()
        await gate.wait()

    r.adapter._process_response = parked_turn  # type: ignore[method-assign]
    turn = asyncio.create_task(r.turn("msg-1", "Keep going", bootstrap=False))
    await turn_started.wait()

    await r.adapter.release_room_resources(ROOM)

    assert r.manager.has_session(ROOM)
    gate.set()
    await turn


async def test_leaving_after_a_release_forgets_the_session(room) -> None:
    r = await room()
    await r.adapter.release_room_resources(ROOM)

    await r.adapter.on_cleanup(ROOM)

    assert (r.adapter._released_sessions, r.adapter._room_workspaces) == ({}, {})
