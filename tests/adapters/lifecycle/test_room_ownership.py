"""Room workspace lifecycle: a room keeps its child and workspace until cleanup provably completes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.core.exceptions import InputNotAccepted
from tests.adapters.hold import Hold
from tests.adapters.lifecycle.kit import RetentionHost, raises_exactly
from tests.adapters.roompeer import (
    CLEANUP_FAILURE,
    PeerCommand,
    write_instruction,
)


class TestRoomOwnership:
    """A room keeps its child and workspace until cleanup provably completes."""

    async def test_completed_turns_retain_children_until_explicit_cleanup(
        self,
        host: RetentionHost,
    ) -> None:
        host.shared = False
        for room in ("room-a", "room-b", "room-c"):
            assert await host.send(room, write_instruction(room)) == [room]
        assert host.exited == [False, False, False]
        assert await host.send("room-a", PeerCommand.READ) == ["Read: room-a"]
        assert host.exited == [False, False, False]
        await host.shutdown()
        assert host.exited == [True, True, True]

    async def test_failed_cleanup_retains_ownership_and_blocks_replacement(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        host.children[0].refuse_close = True
        with pytest.raises(RuntimeError, match=CLEANUP_FAILURE):
            await host.leave("room-a")
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        with pytest.raises(InputNotAccepted, match="is closing"):
            await host.send("room-a", write_instruction("changed"))
        assert host.exited == [False]
        assert (Path(host.workspace("room-a")) / "notes.txt").read_text() == "alpha"
        host.children[0].refuse_close = False
        await host.leave("room-a")
        assert host.exited == [True]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        assert host.exited == [True, False]

    async def test_failed_startup_keeps_membership_claim_until_leave(
        self,
        host: RetentionHost,
    ) -> None:
        host.fail_startup.touch()
        with raises_exactly(host.failed_start):
            await host.send("room-a", write_instruction("alpha"))
        await host.settled("room-a")
        assert host.exited == [True]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        await host.leave("room-a")
        host.fail_startup.unlink()
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    async def test_failed_room_cleanup_can_recover_in_the_same_workspace(
        self,
        host: RetentionHost,
    ) -> None:
        """A retried leave frees the room to rejoin its workspace."""
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        host.children[0].refuse_close = True
        with pytest.raises(RuntimeError, match=CLEANUP_FAILURE):
            await host.leave("room-a")
        host.children[0].refuse_close = False
        await host.leave("room-a")
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
        assert host.exited == [True, False]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))

    async def test_cancelled_startup_keeps_ownership_and_can_recover(
        self,
        host: RetentionHost,
    ) -> None:
        host.hold_startup.touch()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        await host.start_reached("room-a")
        opening.cancel()
        with pytest.raises(asyncio.CancelledError):
            await opening
        host.hold_startup.unlink()
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        assert host.exited.count(False) == 1
        assert host.overlaps == 0
        await host.leave("room-a")
        assert all(host.exited)

    async def test_cancelled_cleanup_can_be_completed_before_transfer(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        barrier = host.children[0].closing = Hold()
        leaving = asyncio.create_task(host.leave("room-a"))
        async with barrier:
            leaving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaving
            assert host.exited == [False]
        await host.leave("room-a")
        assert host.exited == [True]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    async def test_concurrent_leaves_join_one_held_close(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        barrier = host.children[0].closing = Hold()
        first = asyncio.create_task(host.leave("room-a"))
        await barrier.reached.wait()
        second = asyncio.create_task(host.leave("room-a"))
        done, _ = await asyncio.wait({second}, timeout=0.05)
        assert not done
        with pytest.raises(InputNotAccepted, match="is closing"):
            await host.send("room-a", write_instruction("changed"))
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        assert host.exited == [False]
        barrier.released.set()
        await asyncio.gather(first, second)
        assert host.exited == [True]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        assert host.overlaps == 0

    async def test_shutdown_attempts_every_room_and_retains_failed_children(
        self,
        host: RetentionHost,
    ) -> None:
        host.shared = False
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
        host.children[0].refuse_close = True
        with pytest.raises(ExceptionGroup) as failed:
            await host.shutdown()
        assert failed.group_contains(RuntimeError, match=CLEANUP_FAILURE, depth=None)
        assert host.exited == [False, True]
        with pytest.raises(InputNotAccepted):
            await host.send("room-a", PeerCommand.READ)
        assert len(host.children) == 2
        host.children[0].refuse_close = False
        await host.adapter.on_started("Peer", "Restarting after failed cleanup")
        assert host.exited == [True, True]
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
        assert host.overlaps == 0

    async def test_failed_startup_cleanup_blocks_replacement_until_retry(
        self,
        host: RetentionHost,
    ) -> None:
        host.fail_startup.touch()
        host.hold_startup.touch()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        await host.start_reached("room-a")
        host.children[0].refuse_close = True
        host.hold_startup.unlink()
        with raises_exactly(host.failed_start_closing_refused):
            await opening
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        assert host.exited == [False]
        host.fail_startup.unlink()
        host.children[0].refuse_close = False
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        assert host.exited == [True, False]
        assert host.overlaps == 0
