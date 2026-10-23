"""cleanup_all reports every room close failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.omp import OMP_CWD_FLAG
from band.integrations.ompprocess import OMP_SESSION_DIR_FLAG
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import BoomCloseRuntime, occupied_room_ids, run_turn


class TestCleanupAllFailures:
    @pytest.mark.asyncio
    async def test_cleanup_all_raises_every_close_error(
        self, monkeypatch, tmp_path: Path
    ):
        runtimes: dict[str, BoomCloseRuntime] = {}

        def spawn(command, env=None, **_kwargs):
            cwd = next(
                command[index + 1]
                for index, part in enumerate(command)
                if part in {OMP_CWD_FLAG, OMP_SESSION_DIR_FLAG}
            )
            label = "first" if "room-a" in cwd or len(runtimes) == 0 else "second"
            runtime = BoomCloseRuntime()
            runtime.finish.set()
            runtime.label = label
            runtimes[label] = runtime
            return runtime

        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", spawn)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        await run_turn(adapter, FakeAgentTools(), room_id="room-a")
        await run_turn(adapter, FakeAgentTools(), room_id="room-b")
        assert occupied_room_ids(adapter) == {"room-a", "room-b"}
        with pytest.raises(BaseExceptionGroup, match="room cleanup failed") as raised:
            await adapter.cleanup_all()
        assert [error.args for error in raised.value.exceptions] == [
            ("process still alive",),
            ("process still alive",),
        ]
        assert occupied_room_ids(adapter) == {"room-a", "room-b"}
