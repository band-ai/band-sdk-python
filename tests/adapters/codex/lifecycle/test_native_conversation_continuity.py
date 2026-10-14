"""Codex room lifecycle: native conversation continuity."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapter,
    CodexCommand,
    CodexContinuityError,
    CodexSubcommand,
)
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.roompeer import (
    CODEX_HOME,
    NO_ROLLOUT,
    PeerCommand,
    memo_instruction,
    rollout_path,
)


async def lose_process(
    host: RetentionHost,
    room_id: str,
    loss: PeerCommand = PeerCommand.LOSE_TRANSPORT,
) -> str:
    """Lose the room's process mid-turn and return the thread it was on."""
    thread = host.native_id(room_id)
    assert thread is not None
    with pytest.raises(TurnResultAlreadyReported):
        await host.send(room_id, loss)
    await host.settled(room_id)
    return thread


class TestNativeConversationContinuity:
    """A replacement process continues the room native conversation or refuses to run."""

    @pytest.mark.parametrize(
        "loss", [PeerCommand.LOSE_TRANSPORT, PeerCommand.EXIT_PROCESS]
    )
    async def test_a_replacement_process_continues_the_same_native_conversation(
        self, host: RetentionHost, loss: PeerCommand
    ) -> None:
        """The memo lives only in the thread's private context, so recalling it
        on a new process proves the native conversation itself continued."""
        assert await host.send("room-a", memo_instruction("private-7")) == ["Noted."]
        thread = await lose_process(host, "room-a", loss)

        assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: private-7"]
        assert host.native_id("room-a") == thread
        assert host.exited == [True, False]
        assert host.overlaps == 0

    async def test_a_refused_restore_keeps_the_conversation_and_starts_no_other(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", memo_instruction("private-7")) == ["Noted."]
        thread = await lose_process(host, "room-a")
        saved = rollout_path(host.root / CODEX_HOME, thread)
        kept = saved.read_text()
        saved.unlink()

        for _ in range(2):
            with pytest.raises(CodexJsonRpcError, match=NO_ROLLOUT):
                await host.send("room-a", PeerCommand.RECALL)
            await host.settled("room-a")
            assert host.native_id("room-a") == thread
        assert not any(saved.parent.iterdir())

        saved.write_text(kept)
        assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: private-7"]
        assert host.overlaps == 0

    async def test_archiving_a_thread_codex_refuses_to_restore_starts_a_new_one(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", memo_instruction("private-7")) == ["Noted."]
        thread = await lose_process(host, "room-a")
        rollout_path(host.root / CODEX_HOME, thread).unlink()
        with pytest.raises(CodexJsonRpcError, match=NO_ROLLOUT):
            await host.send("room-a", PeerCommand.RECALL)
        await host.settled("room-a")

        await host.send("room-a", f"/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}")

        assert await host.send("room-a", memo_instruction("fresh-1")) == ["Noted."]
        assert host.native_id("room-a") not in (None, thread)

    async def test_restored_threads_refuse_tools_they_were_not_started_with(
        self,
        host: RetentionHost,
    ) -> None:
        assert isinstance(host.adapter, CodexAdapter)
        started_with = await host.send("room-a", PeerCommand.TOOLS)
        thread = await lose_process(host, "room-a")
        original = host.adapter.config
        host.adapter.config = original.model_copy(
            update={
                "additional_dynamic_tools": [
                    {"name": "late_tool", "description": "", "inputSchema": {}}
                ]
            }
        )

        with pytest.raises(CodexContinuityError, match="late_tool"):
            await host.send("room-a", PeerCommand.TOOLS)
        assert host.native_id("room-a") == thread

        host.adapter.config = original
        assert await host.send("room-a", PeerCommand.TOOLS) == started_with
        archive = f"/{CodexCommand.THREAD} {CodexSubcommand.ARCHIVE}"
        await host.send("room-a", archive)
        assert host.native_id("room-a") is None
