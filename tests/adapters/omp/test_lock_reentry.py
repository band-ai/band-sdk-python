"""Permission callbacks may apply a model selection without deadlock."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    PermissionThenReplyRuntime,
    SelectingResolver,
    run_turn,
)


class TestLockReentry:
    @pytest.mark.asyncio
    async def test_permission_resolver_can_apply_model_selection(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = PermissionThenReplyRuntime()
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
            assert any(
                command == OmpCommandName.SET_MODEL for command, _ in runtime.calls
            )
        finally:
            await adapter.cleanup_all()
