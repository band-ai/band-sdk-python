"""Construct an explicit environment for native contract build tools."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class BuildSettings(BaseSettings):
    """Read only the host configuration needed by the selected toolchain."""

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    home: str | None = None
    asdf_dir: str | None = None
    asdf_data_dir: str | None = None
    mix_home: str | None = None
    hex_home: str | None = None
    rebar_cache_dir: str | None = None
    ssl_cert_file: str | None = None
    ssl_cert_dir: str | None = None
    hex_cacerts_path: str | None = None
    path: str = os.defpath

    def environment(self, bin_paths: list[str], *, mix_env: str) -> dict[str, str]:
        """Pass toolchain configuration without inheriting service credentials."""
        result = {
            name.upper(): value
            for name, value in self.model_dump(exclude_none=True).items()
        }
        result.update(
            PATH=os.pathsep.join([*bin_paths, self.path]),
            MIX_ENV=mix_env,
            ERL_FLAGS="+S 2:2 +A 2",
            LANG="C.UTF-8",
            TZ="UTC",
            PYTHONDONTWRITEBYTECODE="1",
            PIP_CONFIG_FILE=os.devnull,
            PIP_DISABLE_PIP_VERSION_CHECK="1",
        )
        return result
