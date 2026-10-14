"""Closing rooms refuse new work throughout interrupted teardown and restart."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.core.exceptions import InputNotAccepted, RoomClosingError
from band.core.types import MessageType
from band.testing.fake_tools import events_of_type
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import RetentionHost, workspace_still_claimed
from tests.adapters.roompeer import (
    CLEANUP_FAILURE,
    WORKSPACE_NOTE,
    PeerCommand,
    read_reply,
    write_instruction,
)
from tests.adapters.waiting import until
from tests.framework_conformance.turnprobes import DispatchingFakeTools


class TestClosingAdmission:
    """The adapter's admission fence lasts until its concrete child is disposed."""

    async def test_held_close_refuses_work_while_another_room_keeps_serving(
        self, host: RetentionHost
    ) -> None:
        host.shared = False
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        native = host.native_id("room-a")
        closing = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave("room-a"))
        async with closing:
            await host.assert_refuses_input(
                "room-a", write_instruction("changed"), error=RoomClosingError
            )
            assert await host.send("room-b", PeerCommand.READ) == [read_reply("bravo")]
            assert host.native_id("room-a") == native
            assert host.exited == [False, False]
        await leaving
        assert host.native_id("room-a") is None
        assert await host.send("room-a", PeerCommand.READ) == [read_reply("alpha")]
        assert host.native_id("room-a") is not None
        assert host.exited == [True, False, False]
        assert host.overlaps == 0

    async def test_cancelled_and_timed_out_cleanup_keep_admission_closed_until_disposal(
        self, host: RetentionHost
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        native = host.native_id("room-a")
        closing = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave("room-a"))
        async with closing:
            leaving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaving
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0):
                    await host.leave("room-a")
            await host.assert_refuses_input(
                "room-a", write_instruction("changed"), error=RoomClosingError
            )
            with workspace_still_claimed():
                await host.send("room-b", write_instruction("bravo"))
            assert host.exited == [False]
            assert host.native_id("room-a") == native
        await host.forgotten("room-a")
        assert host.exited == [True]
        assert await host.send("room-b", PeerCommand.READ) == [read_reply("alpha")]
        assert host.overlaps == 0

    async def test_leave_during_startup_fences_competing_input_before_close(
        self, host: RetentionHost
    ) -> None:
        tools = DispatchingFakeTools(room_id="room-a")
        starting = host.next_start = Hold()
        opening = asyncio.create_task(
            host.deliver("room-a", write_instruction("never written"), tools)
        )
        await starting.arrived()
        closing = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave("room-a"))
        residency = host.bridge.residency("room-a")
        assert residency is not None
        await until(lambda: residency.closing, failure="Leave never fenced the room")
        await host.assert_refuses_input("room-a", write_instruction("competing"))
        starting.released.set()
        async with closing:
            await host.assert_refuses_input("room-a", PeerCommand.READ)
            with workspace_still_claimed():
                await host.send("room-b", write_instruction("bravo"))
            assert host.exited == [False]
        await leaving
        with pytest.raises((asyncio.CancelledError, InputNotAccepted)):
            await opening
        tools.assert_no_messages_sent()
        assert events_of_type(tools, MessageType.ERROR) == []
        assert host.exited == [True]
        assert not (Path(host.workspace("room-a")) / WORKSPACE_NOTE).exists()
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        assert host.overlaps == 0

    async def test_restart_retries_failed_stop_before_readmitting_any_room(
        self, host: RetentionHost
    ) -> None:
        host.shared = False
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        native = host.native_id("room-a")
        child = host.children[0]
        child.refuse_close = True
        with pytest.raises(ExceptionGroup) as failed:
            await host.shutdown()
        assert failed.group_contains(RuntimeError, match=CLEANUP_FAILURE, depth=None)
        child.refuse_close = False
        closing = child.closing = Hold()
        restarting = asyncio.create_task(host.adapter.on_started("Peer", "Restart"))
        async with closing:
            await host.assert_refuses_input("room-a", write_instruction("changed"))
            await host.assert_refuses_input("room-b", write_instruction("bravo"))
            assert host.native_id("room-a") == native
            assert host.exited == [False]
        await restarting
        assert host.exited == [True]
        assert await host.send("room-a", PeerCommand.READ) == [read_reply("alpha")]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        assert host.overlaps == 0

    async def test_stopping_refuses_a_new_room_before_claiming_its_workspace(
        self, host: RetentionHost
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        closing = host.children[0].closing = Hold()
        stopping = asyncio.create_task(host.shutdown())
        async with closing:
            await host.assert_refuses_input("room-b", write_instruction("bravo"))
            assert host.exited == [False]
        await stopping
        assert host.exited == [True]
        await host.adapter.on_started("Peer", "Restart")
        assert await host.send("room-b", PeerCommand.READ) == [read_reply("alpha")]
        assert host.overlaps == 0
