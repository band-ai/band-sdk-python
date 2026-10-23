"""Native OMP launch and provider configuration."""

from __future__ import annotations

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.omp import OMP_APPROVAL_MODE_YOLO


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
                command=("omp", "--mode", "rpc", flag),
                approval_mode=OMP_APPROVAL_MODE_YOLO,
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
