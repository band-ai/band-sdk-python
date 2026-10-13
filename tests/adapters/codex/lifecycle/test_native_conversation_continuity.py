"""Room workspace lifecycle: a replacement process continues the room native conversation or refuses to run."""

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
from tests.adapters.codex.lifecycleprofile import CODEX_HOME
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.roompeer import PeerCommand, memo_instruction


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
        thread = host.native_id("room-a")
        with pytest.raises(TurnResultAlreadyReported):
            await host.send("room-a", loss)
        await host.settled("room-a")

        assert await host.send("room-a", PeerCommand.RECALL) == ["Recalled: private-7"]
        assert host.native_id("room-a") == thread
        assert host.exited == [True, False]
        assert host.overlaps == 0

    async def test_a_refused_restore_keeps_the_conversation_and_starts_no_other(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", memo_instruction("private-7")) == ["Noted."]
        thread = host.native_id("room-a")
        with pytest.raises(TurnResultAlreadyReported):
            await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
        await host.settled("room-a")
        saved = (host.root / CODEX_HOME) / f"{thread}.json"
        kept = saved.read_text()
        saved.unlink()

        for _ in range(2):
            with pytest.raises(CodexJsonRpcError, match="no rollout found"):
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
        thread = host.native_id("room-a")
        with pytest.raises(TurnResultAlreadyReported):
            await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
        await host.settled("room-a")
        ((host.root / CODEX_HOME) / f"{thread}.json").unlink()
        with pytest.raises(CodexJsonRpcError, match="no rollout found"):
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
        thread = host.native_id("room-a")
        with pytest.raises(TurnResultAlreadyReported):
            await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
        await host.settled("room-a")
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
