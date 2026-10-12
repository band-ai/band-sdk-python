"""Room workspace lifecycle: a lost process is reaped while the room keeps its workspace."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import CLIConnectionError

from band.core.protocols import TurnResultAlreadyReported
from tests.adapters.lifecycle.kit import RetentionHost
from tests.adapters.roompeer import PeerCommand, write_instruction


class TestProcessLoss:
    """A lost process is reaped while the room keeps its workspace."""

    @pytest.mark.parametrize(
        "loss", [PeerCommand.LOSE_TRANSPORT, PeerCommand.EXIT_PROCESS]
    )
    async def test_transport_loss_reaps_child_and_preserves_workspace_until_leave(
        self,
        host: RetentionHost,
        loss: PeerCommand,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        original = Path(host.workspace("room-a"))
        host.root = host.root / "changed"
        with pytest.raises((TurnResultAlreadyReported, CLIConnectionError)):
            await host.send("room-a", loss)
        await host.settled("room-a")
        assert host.exited == [True]
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
        assert host.exited == [True, False]

        new_directory = Path(host.workspace("room-a"))
        new_directory.mkdir(parents=True)
        (new_directory / "notes.txt").write_text("bravo")
        await host.leave("room-a")
        assert await host.send("room-a", PeerCommand.READ) == ["Read: bravo"]
        assert (original / "notes.txt").read_text() == "alpha"

    async def test_transport_loss_with_failed_cleanup_can_recover_safely(
        self,
        host: RetentionHost,
    ) -> None:
        assert await host.send("room-a", write_instruction("alpha")) == ["alpha"]
        host.children[0].refuse_close = True
        with pytest.raises((RuntimeError, TurnResultAlreadyReported)):
            await host.send("room-a", PeerCommand.LOSE_TRANSPORT)
        await host.settled("room-a")
        with pytest.raises(ValueError, match="both"):
            await host.send("room-b", write_instruction("bravo"))
        with pytest.raises(RuntimeError, match="cleanup failed"):
            await host.send("room-a", PeerCommand.READ)
        assert len(host.children) == 1
        host.children[0].refuse_close = False
        assert await host.send("room-a", PeerCommand.READ) == ["Read: alpha"]
        assert host.exited == [True, False]
