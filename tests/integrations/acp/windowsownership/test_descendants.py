"""Closing one native Windows ACP tree confirms descendant death and EOF."""

from __future__ import annotations

import os

import pytest

from tests.integrations.acp.acp_toolkit.peer import ExitStage
from tests.integrations.acp.windowsownership.peer import OwnedPeerFactory
from tests.paths import REPO_ROOT


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows job objects")
@pytest.mark.timeout(120)
class TestWindowsACPDescendants:
    async def test_stop_closes_the_agent_and_its_pipe_holder(
        self, owned_pipe_peer: OwnedPeerFactory
    ) -> None:
        async with owned_pipe_peer(ExitStage.EOF) as peer:
            await peer.runtime.start()
            await peer.holder_ready.wait()

            await peer.runtime.stop()
            await peer.holder_closed.wait()

            assert peer.runtime.closed

    async def test_stop_closes_descendants_after_the_agent_already_exited(
        self, owned_pipe_peer: OwnedPeerFactory
    ) -> None:
        async with owned_pipe_peer(ExitStage.EOF) as peer:
            await peer.runtime.start()
            await peer.holder_ready.wait()
            await peer.exit_agent()

            await peer.runtime.stop()
            await peer.holder_closed.wait()

            assert peer.runtime.closed

    async def test_closing_one_tree_preserves_another_runtime(
        self, owned_pipe_peer: OwnedPeerFactory
    ) -> None:
        async with (
            owned_pipe_peer(ExitStage.EOF) as first,
            owned_pipe_peer(ExitStage.EOF) as second,
        ):
            await first.runtime.start()
            await second.runtime.start()
            await first.holder_ready.wait()
            await second.holder_ready.wait()

            await first.runtime.stop()
            await first.holder_closed.wait()
            session = await second.runtime.create_session(
                cwd=str(REPO_ROOT), mcp_servers=[]
            )

            assert first.runtime.closed
            assert session
