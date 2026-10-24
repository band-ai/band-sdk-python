"""Keep native RPC and ACP launch overrides independent in one baseline run."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.integrations.omp import (
    DEFAULT_OMP_ACP_COMMAND,
    omp_command_in_workspace,
    omp_cwd_argument,
)
from band.integrations.ompprocess import (
    DEFAULT_OMP_COMMAND,
    omp_rpc_command_in_workspace,
)
from tests.e2e.baseline.settings import Backends, BaselineSettings
from tests.e2e.baseline.toolkit.omp_credentials import native_omp_command, omp_command

ACP_OVERRIDE = ("custom-omp-acp", *DEFAULT_OMP_ACP_COMMAND[1:])
NATIVE_OVERRIDE = ("custom-omp-native", *DEFAULT_OMP_COMMAND[1:])


class TestNativeOmpCommands:
    @pytest.mark.parametrize(
        ("acp_override", "native_override", "expected_acp", "expected_native"),
        [
            ((), (), DEFAULT_OMP_ACP_COMMAND, DEFAULT_OMP_COMMAND),
            (ACP_OVERRIDE, (), ACP_OVERRIDE, DEFAULT_OMP_COMMAND),
            ((), NATIVE_OVERRIDE, DEFAULT_OMP_ACP_COMMAND, NATIVE_OVERRIDE),
            (ACP_OVERRIDE, NATIVE_OVERRIDE, ACP_OVERRIDE, NATIVE_OVERRIDE),
        ],
        ids=["defaults", "acp-only", "native-only", "both"],
    )
    def test_each_backend_keeps_its_launch_mode_and_override(
        self,
        tmp_path: Path,
        acp_override: tuple[str, ...],
        native_override: tuple[str, ...],
        expected_acp: tuple[str, ...],
        expected_native: tuple[str, ...],
    ) -> None:
        settings = BaselineSettings(
            backends=Backends(
                omp_command=" ".join(acp_override),
                omp_native_command=" ".join(native_override),
            )
        )
        workspace = str(tmp_path)

        acp_launch = omp_command_in_workspace(omp_command(settings), workspace)
        native_launch = omp_rpc_command_in_workspace(
            native_omp_command(settings), workspace
        )

        assert acp_launch == [
            *expected_acp[:2],
            omp_cwd_argument(workspace),
            *expected_acp[2:],
        ]
        assert native_launch == [*expected_native, omp_cwd_argument(workspace)]
