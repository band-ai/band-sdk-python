"""Unproven retirement keeps the room owned across the adapter seam."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.omprpc import OmpCompletionTimeout, OmpProcessError
from band.testing import reported_failures
from tests.adapters.omp.kit import (
    HoldInput,
    RetirementClients,
    StubbornAdapterTool,
    owned_room,
    run_turn,
)
from tests.baseline.tools import BaselineTools
from tests.integrations.omppeer import COMPLETE
from tests.integrations.omprpc.kit import (
    COMPLETION_BUDGET_S,
    NO_CALLBACK_GRACE_S,
    OMP_PEER_COMMAND,
    owned_rpc_process,
    peer_process,
    process_is_alive,
    snapshots_observed,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock

pytest.importorskip("psutil", reason="psutil not installed (band-sdk[omp])")


ROOM_ID = "room-123"
WORKSPACE_CONTENDER_ID = "workspace-contender"
TURN_DEADLINE_S = 30.0


@pytest.mark.looptime(resolution=binary_clock_resolution())
class TestUnprovenRetirement:
    async def test_survivor_blocks_successor_until_callback_finishes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clients = RetirementClients()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", clients)
        hold = StubbornAdapterTool()
        adapter = OmpAdapter(
            OmpAdapterConfig(
                command=(
                    *OMP_PEER_COMMAND,
                    "--scenario",
                    "drain-stubborn-adapter",
                ),
                turn_timeout_s=TURN_DEADLINE_S,
            ),
            workspace_for_room=lambda _room_id: str(tmp_path),
            emit=(),
            additional_tools=[(HoldInput, hold.hold)],
        )

        async with controlled_clock() as clock:
            failed_tools = BaselineTools()
            try:
                async with clock.background(
                    run_turn(adapter, failed_tools, room_id=ROOM_ID)
                ) as running:
                    try:
                        runtime = await clients.created.get()
                        await clock.run_io(hold.entered.wait())
                        await snapshots_observed(runtime, count=2)
                        process = owned_rpc_process(runtime)
                        failed_peer = await peer_process(runtime)
                        assert not running.done()
                        assert not hold.cancel_seen.is_set()
                        runtime.callback_exit_timeout_s = NO_CALLBACK_GRACE_S

                        await clock.elapse(COMPLETION_BUDGET_S)
                        assert hold.cancel_seen.is_set()

                        # Prove deadline cancellation before real process cleanup
                        # is allowed to make the clock progress again.
                        assert not hold.finished.is_set()
                        with pytest.raises(OmpCompletionTimeout):
                            await clock.run_io(running)

                        assert [
                            failure["provider"]
                            for failure in reported_failures(failed_tools)
                        ] == ["omp"]
                        assert not failed_tools.turn.replied
                        room = owned_room(adapter, ROOM_ID)
                        assert room.runtime is runtime
                        assert process.returncode is not None
                        assert not await asyncio.to_thread(
                            process_is_alive, failed_peer
                        )
                        assert clients.plugins[0].is_dir()

                        blocked_tools = BaselineTools()
                        with pytest.raises(OmpProcessError):
                            await clock.run_io(
                                run_turn(
                                    adapter,
                                    blocked_tools,
                                    bootstrap=False,
                                    room_id=ROOM_ID,
                                )
                            )
                        assert owned_room(adapter, ROOM_ID) is room
                        assert clients.clients == [runtime]
                        assert not blocked_tools.turn.replied
                        assert not hold.finished.is_set()
                        assert clients.plugins[0].is_dir()

                        contender_tools = BaselineTools()
                        with pytest.raises(ValueError):
                            await run_turn(
                                adapter,
                                contender_tools,
                                room_id=WORKSPACE_CONTENDER_ID,
                            )
                        assert clients.clients == [runtime]
                        assert owned_room(adapter, ROOM_ID) is room
                        assert not contender_tools.turn.replied

                        await hold.release()
                        await hold.retired.wait()
                        assert hold.finished.is_set()

                        recovered_tools = BaselineTools()
                        async with clock.background(
                            run_turn(
                                adapter,
                                recovered_tools,
                                bootstrap=False,
                                room_id=ROOM_ID,
                            )
                        ) as recovering:
                            replacement_runtime = await clients.created.get()
                            assert hold.finished.is_set()
                            assert process.returncode is not None
                            assert not clients.plugins[0].exists()
                            await clock.run_io(recovering)

                        assert recovered_tools.chat == [COMPLETE]
                        assert recovered_tools.turn.replied
                        assert recovered_tools.turn.complete
                        assert not reported_failures(recovered_tools)
                        assert not failed_tools.turn.replied
                        assert not blocked_tools.turn.replied
                        assert len(clients.clients) == 2
                        replacement = owned_room(adapter, ROOM_ID)
                        assert replacement is not room
                        assert replacement.runtime is replacement_runtime
                        recovered_peer = await peer_process(replacement.runtime)
                        assert recovered_peer != failed_peer
                        assert await asyncio.to_thread(process_is_alive, recovered_peer)
                    finally:
                        # Release external work before background() cancels and
                        # joins the turn, including a failure during startup.
                        await hold.release()
            finally:
                await clock.run_io(adapter.cleanup_all())
