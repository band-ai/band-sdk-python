"""A room keeps its agent process until that process is confirmed closed."""

from __future__ import annotations

import asyncio

import pytest

from band.core.retention import Residency
from band.integrations.acp import client_adapter
from band.integrations.acp.client_adapter import ACPClientAdapter
from tests.adapters.roompeer import CLEANUP_FAILURE
from tests.integrations.acp.acp_toolkit import DEFAULT_ROOM
from tests.integrations.acp.acp_toolkit.harness import send_to
from tests.integrations.acp.acp_toolkit.lifecycle import FailingExits
from tests.integrations.acp.acp_toolkit.peer import ExitStage, stdio_peer_config
from tests.integrations.acp.acp_toolkit.pipes import PipeProcessFactory
from tests.integrations.acp.roomownership.kit import runtime_warnings
from tests.paths import REPO_ROOT


class TestProcessClosure:
    """A room keeps its agent process until that process is confirmed closed."""

    @pytest.mark.parametrize(
        "exit_before_cleanup",
        [False, True],
        ids=["exits-at-close", "exited-before-close"],
    )
    async def test_a_descendant_holding_the_agents_pipes_keeps_its_room_owned(
        self, pipe_process: PipeProcessFactory, exit_before_cleanup: bool
    ) -> None:
        async with pipe_process() as peer:
            await peer.start()
            room = peer.adapter._rooms[DEFAULT_ROOM]
            runtime = room.runtime
            assert runtime is not None
            if exit_before_cleanup:
                await peer.exit_agent()

            cleanup = peer.begin_cleanup()
            await peer.closing.wait()
            await peer.exited.wait()
            if exit_before_cleanup:
                await peer.wait_for_stdout_cleanup(cleanup)
            cleanup.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cleanup

            assert not peer.stdout_closed.is_set()
            assert peer.adapter._rooms[DEFAULT_ROOM] is room
            assert room.runtime is runtime and not runtime.closed
            assert room.retained.snapshot.state is Residency.SUSPENDING

            peer.release_holder()
            await peer.adapter.on_cleanup(DEFAULT_ROOM)
            await peer.stdout_closed.wait()

            assert runtime.closed
            assert DEFAULT_ROOM not in peer.adapter._rooms

    @pytest.mark.asyncio
    async def test_a_retry_after_a_failed_exit_kills_the_running_agent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The agent's exit fails and leaves it running; a retry kills that same
        process and confirms its closure before the room is released."""
        adapter = ACPClientAdapter(
            stdio_peer_config(ExitStage.EOF, []),
            workspace_for_room=lambda room_id: str(REPO_ROOT),
        )
        async with FailingExits() as spawns:
            monkeypatch.setattr(client_adapter, "spawn_agent_process", spawns)
            await adapter.on_started("Stdio peer", "Real subprocess")
            try:
                await send_to(adapter, DEFAULT_ROOM)
                room = adapter._rooms[DEFAULT_ROOM]
                runtime = room.runtime
                [child] = spawns.children
                process = child.process

                with pytest.raises(RuntimeError, match=CLEANUP_FAILURE):
                    await adapter.on_cleanup(DEFAULT_ROOM)

                assert process.returncode is None
                assert room.runtime is runtime

                await adapter.on_cleanup(DEFAULT_ROOM)

                assert process.returncode is not None
                assert DEFAULT_ROOM not in adapter._rooms
                assert runtime_warnings(caplog) == [
                    f"ACP agent subprocess {process.pid} failed to exit",
                    f"Killing ACP agent subprocess {process.pid} after its exit failed",
                ]
            finally:
                await adapter.cleanup_all()
