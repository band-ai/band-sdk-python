"""Native OMP launch and provider configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.testing import FakeAgentTools, failure_reports
from tests.adapters.omp.kit import AutomaticThinkingRuntime, run_turn


class TestConfiguration:
    def test_native_workspace_is_selected_by_the_rpc_launch_command(self) -> None:
        command = OmpAdapter()._spawn_command("/workspace")

        assert command[-1] == "--cwd=/workspace"

    @pytest.mark.parametrize(
        "flag",
        ["--yolo", "--auto-approve", "--approval-mode=yolo", "--approval-mode=write"],
    )
    def test_launch_flags_cannot_bypass_explicit_approval_setting(self, flag):
        with pytest.raises(ValueError, match="Unsafe OMP"):
            OmpAdapterConfig(
                command=("omp", "--mode", "rpc", flag), approval_mode="yolo"
            )

    def test_explicit_provider_environment_wins_without_exposing_secrets(self):
        adapter = OmpAdapter(
            OmpAdapterConfig(
                model="openai/test",
                api_key="derived-secret",
                env={"OPENAI_API_KEY": "explicit-secret"},
            )
        )
        assert adapter._spawn_env()["OPENAI_API_KEY"] == "explicit-secret"
        assert "secret" not in repr(adapter.config)

    @pytest.mark.asyncio
    async def test_auto_thinking_can_complete_with_a_concrete_effective_level(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = AutomaticThinkingRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), reasoning_effort="auto"), emit=()
        )
        tools = FakeAgentTools()
        try:
            await run_turn(adapter, tools)
            assert tools.turn.replied
            assert not failure_reports(tools)
        finally:
            await adapter.cleanup_all()
