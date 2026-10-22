"""Native OMP launch settings: command shape and provider environment."""

from __future__ import annotations

from band.integrations.omp import (
    OMP_APPROVAL_MODE_ALWAYS_ASK,
    OMP_APPROVAL_MODE_FLAG,
    finalize_omp_command,
    omp_command_in_workspace,
    omp_cwd_argument,
)
from band.integrations.ompprocess import (
    DEFAULT_OMP_COMMAND,
    OMP_NATIVE_PINNED_PACKAGE,
    omp_rpc_command_in_workspace,
    omp_runtime_env,
)
from tests.paths import CI_SCRIPTS


class TestNativeLaunchSettings:
    def test_default_command_is_safe_after_finalize(self) -> None:
        assert finalize_omp_command(DEFAULT_OMP_COMMAND)[-2:] == [
            OMP_APPROVAL_MODE_FLAG,
            OMP_APPROVAL_MODE_ALWAYS_ASK,
        ]

    def test_workspace_is_selected_without_a_subprocess_cwd(self) -> None:
        assert omp_command_in_workspace(("bunx", "omp", "acp", "-v"), "/w") == [
            "bunx",
            "omp",
            "acp",
            "--cwd=/w",
            "-v",
        ]
        assert omp_rpc_command_in_workspace(
            ("bunx", *DEFAULT_OMP_COMMAND, "-v"), "/w"
        ) == [
            "bunx",
            *DEFAULT_OMP_COMMAND,
            "-v",
            omp_cwd_argument("/w"),
        ]

    def test_runtime_env_routes_the_selected_provider_key(self) -> None:
        assert omp_runtime_env(env=None, model="openai/custom", api_key="secret") == {
            "OPENAI_API_KEY": "secret"
        }

    def test_explicit_env_overrides_the_derived_credential(self) -> None:
        env = omp_runtime_env(
            env={"OPENAI_API_KEY": "override", "EXTRA": "1"},
            model="openai/custom",
            api_key="secret",
        )
        assert env == {"OPENAI_API_KEY": "override", "EXTRA": "1"}

    def test_no_credentials_and_no_env_means_inherit(self) -> None:
        assert omp_runtime_env(env=None, model=None, api_key=None) is None
        assert omp_runtime_env(env={}, model="openai/custom", api_key=None) is None

    def test_setup_omp_script_pins_the_native_package(self) -> None:
        """The one CI binary serves the native cells, so it carries the native pin."""
        script = (CI_SCRIPTS / "setup-omp.sh").read_text()
        assert OMP_NATIVE_PINNED_PACKAGE in script
