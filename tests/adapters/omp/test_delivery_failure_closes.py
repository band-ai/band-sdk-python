"""A delivery failure retires the room process."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import occupied_room_ids
from tests.adapters.omp.failures import DeliveryCrashRuntime
from tests.adapters.omp.turns import run_turn


class TestDeliveryFailureCloses:
    @pytest.mark.usefixtures("native_startup_ready")
    @pytest.mark.asyncio
    async def test_delivery_failure_closes_the_room(self, monkeypatch, tmp_path: Path):
        runtime = DeliveryCrashRuntime()
        monkeypatch.setattr(
            "band.adapters.omp.lifecycle.OmpRpcClient", lambda *a, **kw: runtime
        )
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        with pytest.raises(RuntimeError, match="delivery rejected"):
            await run_turn(adapter, FakeAgentTools())
        assert runtime.closed
        assert occupied_room_ids(adapter) == set()
