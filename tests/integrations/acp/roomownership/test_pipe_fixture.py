"""The pipe fixture settles descendants even when startup never completes."""

from __future__ import annotations

import asyncio

import pytest

from tests.integrations.acp.acp_toolkit.pipes import PipeProcessFactory


class TestPipeFixture:
    async def test_process_exit_does_not_close_inherited_stdout(
        self, pipe_process: PipeProcessFactory
    ) -> None:
        async with pipe_process() as peer:
            await peer.start()
            await peer.exit_agent()
            assert peer.returncode is not None
            assert not peer.stdout_closed.is_set()

            peer.release_holder()
            await peer.stdout_closed.wait()

    @pytest.mark.parametrize(
        "cancel_start", [False, True], ids=["failure", "cancellation"]
    )
    async def test_teardown_releases_a_holder_created_after_startup_is_abandoned(
        self, pipe_process: PipeProcessFactory, cancel_start: bool
    ) -> None:
        with pytest.raises(RuntimeError, match="test interrupted"):
            async with pipe_process() as peer:
                peer.hold_startup()
                starting = peer.begin_start()
                await peer.agent_connected.wait()
                assert not peer.holder_ready.is_set()
                if cancel_start:
                    starting.cancel()
                    await peer.closing.wait()
                    assert not peer.transport_closing.is_set()
                raise RuntimeError("test interrupted")

        assert peer.holder_ready.is_set()
        assert peer.exited.is_set()
        assert peer.stdout_closed.is_set()
        assert peer.stderr_closed.is_set()
        assert starting.done()
        if cancel_start:
            with pytest.raises(asyncio.CancelledError):
                await starting
