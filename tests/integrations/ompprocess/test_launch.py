"""Native OMP launch settings: command shape and provider environment."""

from __future__ import annotations

from band.integrations.ompprocess import omp_runtime_env


class TestNativeLaunchSettings:
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
