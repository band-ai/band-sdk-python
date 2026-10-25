"""Same-task permission callbacks may reenter room selection without deadlock."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.models import PermissionThenReplyRuntime, SelectingResolver
from tests.adapters.omp.runtime import AdapterFactory
from tests.adapters.omp.turns import run_turn


class TestLockReentry:
    @pytest.mark.asyncio
    async def test_permission_resolver_can_apply_model_selection(
        self, tmp_path: Path, adapter_for: AdapterFactory
    ):
        runtime = PermissionThenReplyRuntime()
        runtime.finish.set()
        resolver = SelectingResolver()
        adapter = adapter_for(
            runtime,
            config=OmpAdapterConfig(cwd=str(tmp_path), model="openai/original"),
            resolve_permission=resolver,
            emit=(),
        )
        resolver.adapter = adapter
        await run_turn(adapter, FakeAgentTools())
        assert resolver.called.is_set()
        assert runtime.model == ("openai", "other")
        assert any(command == OmpCommandName.SET_MODEL for command, _ in runtime.calls)
