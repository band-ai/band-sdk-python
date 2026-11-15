"""Select the Antigravity executable for the host without changing overrides."""

from __future__ import annotations

import pytest

from band.adapters.antigravity_acp import (
    AntigravityACPAdapterConfig,
    default_antigravity_command,
)


class TestAntigravityLaunch:
    @pytest.mark.parametrize(
        ("platform", "command"),
        [
            ("linux", ("agy_acp_server.par", "--uid=")),
            ("darwin", ("agy_acp_server.par",)),
            ("win32", ("agy_acp_server.exe",)),
        ],
    )
    def test_default_command_matches_the_host(
        self, platform: str, command: tuple[str, ...]
    ) -> None:
        assert default_antigravity_command(platform) == command

    def test_explicit_command_replaces_the_default(self) -> None:
        assert AntigravityACPAdapterConfig(command=("custom-agent",)).command == (
            "custom-agent",
        )
