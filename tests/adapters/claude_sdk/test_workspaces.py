"""Room workspace ownership through the real SDK session lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
import pytest_asyncio
from claude_agent_sdk import ClaudeAgentOptions

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from band.integrations.claude_sdk.session_manager import ClaudeSessionManager
from band.workspaces import RoomWorkspaces, create_room_workspace_resolver
from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


@pytest_asyncio.fixture(loop_scope="function")
async def sessions(
    claude: FakeClaude, tmp_path: Path
) -> AsyncIterator[ClaudeSessionManager]:
    manager = ClaudeSessionManager(
        ClaudeAgentOptions(),
        workspaces=RoomWorkspaces(lambda _: str(tmp_path / "shared")),
    )
    yield manager
    claude.refuse_close = False
    for session in claude.sessions:
        session.refuse_close = False
    if claude.connecting is not None:
        claude.connecting.released.set()
    if claude.closing is not None:
        claude.closing.released.set()
    await manager.stop()


async def test_default_rooms_have_distinct_workspaces(
    claude_room: OpenRoom,
    claude: FakeClaude,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    first = await claude_room()
    second = first.beside("room-2")
    claude.script([first.model_reply("First.")], [second.model_reply("Second.")])
    await first.send("hi")
    await second.send("hi")
    assert claude.session_workspaces == [
        str(tmp_path / ".band-workspaces" / "room-1"),
        str(tmp_path / ".band-workspaces" / "room-2"),
    ]


async def test_explicit_cwd_preserves_shared_directory(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    first = await claude_room(ClaudeSDKAdapterConfig(cwd=tmp_path))
    second = first.beside("room-2")
    claude.script([first.model_reply("First.")], [second.model_reply("Second.")])
    await first.send("hi")
    await second.send("hi")
    assert claude.session_workspaces == [str(tmp_path), str(tmp_path)]


def test_cwd_and_resolver_are_mutually_exclusive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="either cwd or workspace_for_room"):
        ClaudeSDKAdapter(
            ClaudeSDKAdapterConfig(cwd=tmp_path),
            workspace_for_room=create_room_workspace_resolver(tmp_path),
        )


async def test_retired_session_keeps_its_workspace_until_leave(
    claude: FakeClaude, tmp_path: Path
) -> None:
    path = tmp_path / "original"
    manager = ClaudeSessionManager(
        ClaudeAgentOptions(), workspaces=RoomWorkspaces(lambda _: str(path))
    )
    try:
        await manager.get_or_create_session("room-a")
        marker = path / "notes.txt"
        marker.write_text("keep me")
        await manager.cleanup_session("room-a", release_workspace=False)
        path = tmp_path / "changed"
        await manager.get_or_create_session("room-a", resume_session_id="saved")
        assert claude.session_workspaces == [str(marker.parent), str(marker.parent)]
        assert claude.resumed == [None, "saved"]
        await manager.cleanup_session("room-a")
        await manager.get_or_create_session("room-a")
        assert claude.session_workspaces[-1] == str(path)
        assert marker.read_text() == "keep me"
    finally:
        await manager.stop()


async def test_collision_is_rejected_until_owner_leaves(
    sessions: ClaudeSessionManager, claude: FakeClaude
) -> None:
    await sessions.get_or_create_session("room-a")
    with pytest.raises(ValueError, match="both"):
        await sessions.get_or_create_session("room-b")
    await sessions.cleanup_session("room-a")
    await sessions.get_or_create_session("room-b")
    assert [session.alive for session in claude.sessions] == [False, True]


async def test_cancelled_startup_keeps_claim_and_reuses_client(
    sessions: ClaudeSessionManager, claude: FakeClaude
) -> None:
    hold = claude.connecting = Hold()
    opening = asyncio.create_task(sessions.get_or_create_session("room-a"))
    await hold.reached.wait()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    hold.released.set()
    with pytest.raises(ValueError, match="both"):
        await sessions.get_or_create_session("room-b")
    await sessions.get_or_create_session("room-a")
    assert len(claude.sessions) == 1
    assert claude.sessions[0].alive


async def test_failed_startup_closes_transport_but_keeps_membership_claim(
    sessions: ClaudeSessionManager, claude: FakeClaude
) -> None:
    claude.refuse_connect = True
    with pytest.raises(Exception, match="startup"):
        await sessions.get_or_create_session("room-a")
    assert not claude.sessions[0].alive
    with pytest.raises(ValueError, match="both"):
        await sessions.get_or_create_session("room-b")
    await sessions.cleanup_session("room-a")
    claude.refuse_connect = False
    await sessions.get_or_create_session("room-b")
    assert claude.sessions[-1].alive


async def test_failed_cleanup_blocks_reuse_and_can_be_retried(
    sessions: ClaudeSessionManager, claude: FakeClaude
) -> None:
    await sessions.get_or_create_session("room-a")
    claude.refuse_close = True
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await sessions.cleanup_session("room-a")
    with pytest.raises(ValueError, match="both"):
        await sessions.get_or_create_session("room-b")
    with pytest.raises(RuntimeError, match="cleanup failed"):
        await sessions.get_or_create_session("room-a")
    assert len(claude.sessions) == 1
    claude.refuse_close = False
    await sessions.cleanup_session("room-a")
    await sessions.get_or_create_session("room-b")
    assert [session.alive for session in claude.sessions] == [False, True]


async def test_cancelled_cleanup_finishes_before_claim_transfer(
    sessions: ClaudeSessionManager, claude: FakeClaude
) -> None:
    await sessions.get_or_create_session("room-a")
    hold = claude.closing = Hold()
    leaving = asyncio.create_task(sessions.cleanup_session("room-a"))
    await hold.reached.wait()
    leaving.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leaving
    hold.released.set()
    await sessions.get_or_create_session("room-b")
    assert [session.alive for session in claude.sessions] == [False, True]


async def test_stop_attempts_all_rooms_and_retries_failed_cleanup(
    claude: FakeClaude, tmp_path: Path
) -> None:
    manager = ClaudeSessionManager(
        ClaudeAgentOptions(),
        workspaces=RoomWorkspaces(create_room_workspace_resolver(tmp_path)),
    )
    await manager.get_or_create_session("room-a")
    await manager.get_or_create_session("room-b")
    claude.sessions[0].refuse_close = True
    try:
        with pytest.raises(ExceptionGroup, match="cleanup failed"):
            await manager.stop()
        assert [session.alive for session in claude.sessions] == [True, False]
        assert manager.get_active_rooms() == ["room-a"]
    finally:
        claude.sessions[0].refuse_close = False
        await manager.stop()
    assert not claude.sessions[0].alive


async def test_adapter_restart_preserves_workspace_files(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(
        workspace_for_room=create_room_workspace_resolver(tmp_path)
    )
    claude.script([room.model_reply("Before.")], [room.model_reply("After.")])
    await room.send("hi")
    marker = Path(claude.session_workspaces[0]) / "notes.txt"
    marker.write_text("keep me")
    await room.adapter.cleanup_all()
    await room.adapter.on_started("Test Agent", "An agent under test")
    await room.send("hi again")
    assert claude.session_workspaces == [str(marker.parent), str(marker.parent)]
    assert marker.read_text() == "keep me"


async def test_restart_cannot_replace_a_manager_with_failed_cleanup(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(
        workspace_for_room=create_room_workspace_resolver(tmp_path)
    )
    claude.script([room.model_reply("Before.")], [room.model_reply("After.")])
    await room.send("hi")
    claude.refuse_close = True
    try:
        with pytest.raises(ExceptionGroup, match="cleanup failed"):
            await room.adapter.cleanup_all()
        with pytest.raises(ExceptionGroup, match="cleanup failed"):
            await room.adapter.on_started("Test Agent", "An agent under test")
        assert len(claude.sessions) == 1
        assert claude.sessions[0].alive
    finally:
        claude.refuse_close = False
    await room.adapter.on_started("Test Agent", "An agent under test")
    await room.send("hi again")
    assert [session.alive for session in claude.sessions] == [False, True]
    assert len(set(claude.session_workspaces)) == 1
