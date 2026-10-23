"""RPC callback tasks may reenter room selection without deadlock."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    OtherTaskUiRuntime,
    SelectingResolver,
    requests_named,
    run_turn,
)


class TestCallbackTaskReentry:
    @pytest.mark.asyncio
    async def test_permission_apply_on_callback_task_updates_the_live_model(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = OtherTaskUiRuntime()
        runtime.finish.set()
        resolver = SelectingResolver()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            resolve_permission=resolver,
            emit=(),
        )
        resolver.adapter = adapter
        try:
            await run_turn(adapter, FakeAgentTools())
            assert resolver.called.is_set()
            assert runtime.model == ("openai", "other")
            assert requests_named(runtime, OmpCommandName.SET_MODEL)[-1] == {
                "provider": "openai",
                "modelId": "other",
            }
        finally:
            await adapter.cleanup_all()
