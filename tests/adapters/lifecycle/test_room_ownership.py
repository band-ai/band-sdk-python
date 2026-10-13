"""Room workspace lifecycle: a room keeps its child and workspace until cleanup provably completes."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.codex import CodexAdapter
from band.core.exceptions import InputNotAccepted
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.lifecycle.kit import (
    RetentionHost,
)
from tests.adapters.roompeer import PeerCommand, memo_instruction, write_instruction


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
        with pytest.raises(RuntimeError, match="cleanup failed"):
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
        with pytest.raises(Exception, match="startup failed"):
            await host.send("room-a", write_instruction("alpha"))
        await host.settled("room-a")
        assert host.exited == [True]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        await host.leave("room-a")
        host.fail_startup.unlink()
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    @pytest.mark.parametrize("host", ["claude"], indirect=True)
    async def test_failed_room_cleanup_can_recover_in_the_same_workspace(
        self,
        host: RetentionHost,
    ) -> None:
        """Pre-ownership behavior; superseded by the Codex cell below."""
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        host.children[0].refuse_close = True
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await host.leave("room-a")
        host.children[0].refuse_close = False
        await host.leave("room-a")
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
        assert host.exited == [True, False]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))

    @pytest.mark.parametrize("host", ["codex"], indirect=True)
    async def test_a_failed_leave_is_final_until_retried_then_rejoin_starts_fresh(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", memo_instruction("kept")) == ["Noted."]
        left_thread = host.native_id("room-a")
        host.children[0].refuse_close = True
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await host.leave("room-a")
        host.children[0].refuse_close = False
        with pytest.raises(InputNotAccepted, match="is closing"):
            await host.send("room-a", PeerCommand.RECALL)
        assert host.native_id("room-a") == left_thread
        await host.leave("room-a")
        assert host.native_id("room-a") is None
        assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: None"]
        assert host.native_id("room-a") not in (None, left_thread)
        assert host.exited == [True, False]
        assert host.overlaps == 0

    async def test_cancelled_startup_keeps_ownership_and_can_recover(
        self,
        host: RetentionHost,
    ) -> None:
        host.hold_startup.touch()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        while not (Path(host.workspace("room-a")) / "starting.txt").exists():
            await asyncio.sleep(0.01)
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
        assert failed.group_contains(RuntimeError, match="cleanup failed", depth=None)
        assert host.exited == [False, True]
        with pytest.raises(ExceptionGroup):
            await host.adapter.on_started("Peer", "Restarting after failed cleanup")
        assert host.exited == [False, True]
        host.children[0].refuse_close = False
        await host.adapter.on_started("Peer", "Restarting after successful cleanup")
        assert host.exited == [True, True]
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]

    async def test_failed_startup_cleanup_blocks_replacement_until_retry(
        self,
        host: RetentionHost,
    ) -> None:
        host.fail_startup.touch()
        host.hold_startup.touch()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        while not (Path(host.workspace("room-a")) / "starting.txt").exists():
            await asyncio.sleep(0.01)
        host.children[0].refuse_close = True
        host.hold_startup.unlink()
        # The opening turn reports its own failure; Codex reports the failed
        # cleanup that follows to the next operation.
        with pytest.raises(RuntimeError, match="startup failed|cleanup failed"):
            await opening
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        host.fail_startup.unlink()
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await host.send("room-a", write_instruction("alpha"))
        assert host.exited == [False]
        host.children[0].refuse_close = False
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        assert host.exited == [True, False]

    @pytest.mark.parametrize("host", ["codex"], indirect=True)
    async def test_close_timeout_retains_ownership_until_retry(
        self,
        host: RetentionHost,
    ) -> None:
        assert isinstance(host.adapter, CodexAdapter)
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        host.adapter.config = host.adapter.config.model_copy(
            update={"client_close_timeout_s": 0.05}
        )
        barrier = host.children[0].closing = Hold()
        with pytest.raises(TimeoutError):
            await host.leave("room-a")
        await barrier.reached.wait()
        assert host.exited == [False]
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        with pytest.raises(InputNotAccepted, match="is closing"):
            await host.send("room-a", write_instruction("changed"))
        assert host.exited == [False]
        host.adapter.config = host.adapter.config.model_copy(
            update={"client_close_timeout_s": None}
        )
        barrier.released.set()
        await host.leave("room-a")
        assert host.exited == [True]
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]

    @pytest.mark.parametrize("host", ["codex"], indirect=True)
    async def test_leaving_during_startup_closes_the_half_started_process(
        self,
        host: RetentionHost,
    ) -> None:
        assert isinstance(host.adapter, CodexAdapter)
        host.adapter.config = host.adapter.config.model_copy(
            update={"turn_settle_timeout_s": 0.01}
        )
        starting = host.next_start = Hold()
        opening = asyncio.create_task(host.send("room-a", write_instruction("alpha")))
        await starting.reached.wait()

        await host.leave("room-a")

        with pytest.raises(asyncio.CancelledError):
            await opening
        assert host.exited == [True]
        assert host.native_id("room-a") is None
        assert await host.send("room-b", write_instruction("bravo")) == ["bravo"]
