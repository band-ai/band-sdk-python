"""Room workspace ownership through the real SDK session lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from claude_agent_sdk import CLIConnectionError

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from band.workspaces import create_room_workspace_resolver
from tests.adapters.claude_sdk.fakecli import FakeClaude, Hold
from tests.adapters.claude_sdk.helpers import ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


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


async def test_cancelled_turn_keeps_its_workspace_until_leave(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    path = tmp_path / "original"
    room = await claude_room(workspace_for_room=lambda _: str(path))
    abandoned = Hold()
    claude.script(
        [room.model_reply("First.")],
        [abandoned, room.model_reply("Never sent.")],
        [room.model_reply("Resumed.")],
        [room.model_reply("Rejoined.")],
    )
    await room.send("hi")
    marker = path / "notes.txt"
    marker.write_text("keep me")
    path = tmp_path / "changed"
    message = room.send_in_background("long job")
    async with abandoned:
        message.cancel()
        with pytest.raises(asyncio.CancelledError):
            await message
    await room.send("again")
    assert claude.session_workspaces == [str(marker.parent), str(marker.parent)]
    assert claude.resumed == [None, "sess-1"]
    assert [session.alive for session in claude.sessions] == [False, True]

    await room.leave()
    await room.send("I'm back")
    assert claude.session_workspaces[-1] == str(path)
    assert claude.resumed[-1] is None
    assert room.chat == ["First.", "Resumed.", "Rejoined."]
    assert room.failures == []
    assert marker.read_text() == "keep me"


async def test_collision_is_rejected_until_owner_leaves(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    first = await claude_room(workspace_for_room=lambda _: str(tmp_path / "shared"))
    second = first.beside("room-2")
    claude.script([first.model_reply("First.")], [second.model_reply("Second.")])
    await first.send("hi")
    with pytest.raises(ValueError, match="both"):
        await second.send("hi")
    assert second.chat == []
    assert len(second.failures) == 1
    await first.leave()
    await second.send("again")
    assert second.chat == ["Second."]
    assert [session.alive for session in claude.sessions] == [False, True]


async def test_cancelled_startup_keeps_claim_and_reuses_client(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(workspace_for_room=lambda _: str(tmp_path / "shared"))
    hold = claude.connecting = Hold()
    opening = room.send_in_background("hi")
    async with hold:
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
    with pytest.raises(ValueError, match="both"):
        await room.beside("room-2").send("hi")
    claude.script([room.model_reply("Connected.")])
    await room.send("again")
    assert len(claude.sessions) == 1
    assert claude.sessions[0].alive
    assert room.chat == ["Connected."]
    assert room.failures == []


async def test_failed_startup_closes_transport_but_keeps_membership_claim(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(workspace_for_room=lambda _: str(tmp_path / "shared"))
    second = room.beside("room-2")
    claude.refuse_connect = True
    try:
        with pytest.raises(CLIConnectionError, match="startup"):
            await room.send("hi")
        assert not claude.sessions[0].alive
        assert len(room.failures) == 1
        with pytest.raises(ValueError, match="both"):
            await second.send("hi")
        await room.leave()
    finally:
        claude.refuse_connect = False
    claude.script([second.model_reply("Connected.")])
    await second.send("again")
    assert second.chat == ["Connected."]
    assert claude.sessions[-1].alive


async def test_failed_cleanup_blocks_reuse_and_can_be_retried(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(workspace_for_room=lambda _: str(tmp_path / "shared"))
    second = room.beside("room-2")
    claude.script([room.model_reply("First.")], [second.model_reply("Second.")])
    await room.send("hi")
    claude.refuse_close = True
    try:
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await room.leave()
        with pytest.raises(ValueError, match="both"):
            await second.send("hi")
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await room.send("again")
        assert len(claude.sessions) == 1
        assert claude.sessions[0].alive
        assert room.chat == ["First."]
        assert second.chat == []
    finally:
        claude.refuse_close = False
    await room.leave()
    await second.send("again")
    assert second.chat == ["Second."]
    assert [session.alive for session in claude.sessions] == [False, True]


async def test_cancelled_cleanup_finishes_before_claim_transfer(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(workspace_for_room=lambda _: str(tmp_path / "shared"))
    second = room.beside("room-2")
    claude.script([room.model_reply("First.")], [second.model_reply("Second.")])
    await room.send("hi")
    hold = claude.closing = Hold()
    leaving = asyncio.create_task(room.leave())
    async with hold:
        leaving.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leaving
    await second.send("hi")
    assert second.chat == ["Second."]
    assert [session.alive for session in claude.sessions] == [False, True]


async def test_stop_attempts_all_rooms_and_retries_failed_cleanup(
    claude_room: OpenRoom, claude: FakeClaude, tmp_path: Path
) -> None:
    room = await claude_room(
        workspace_for_room=create_room_workspace_resolver(tmp_path)
    )
    second = room.beside("room-2")
    claude.script([room.model_reply("First.")], [second.model_reply("Second.")])
    await room.send("hi")
    await second.send("hi")
    claude.sessions[0].refuse_close = True
    try:
        with pytest.raises(ExceptionGroup, match="cleanup failed"):
            await room.adapter.cleanup_all()
        assert [session.alive for session in claude.sessions] == [True, False]
    finally:
        claude.sessions[0].refuse_close = False
        await room.adapter.cleanup_all()
    assert all(not session.alive for session in claude.sessions)


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
