"""Native metadata options must reach models without becoming runtime options."""

from __future__ import annotations

import pytest

from band.integrations.ompcatalog import OmpListingUnavailable, model_listing_command


class TestOmpCatalogCommand:
    @pytest.mark.parametrize(
        ("command", "expected"),
        [
            (
                ("omp", "--config", "overlay.yml", "--mode", "rpc", "--no-extensions"),
                (
                    "omp",
                    "models",
                    "--json",
                    "--config",
                    "overlay.yml",
                    "--no-extensions",
                ),
            ),
            (
                (
                    "omp",
                    "--config=overlay.yml",
                    "--mode=rpc",
                    "--extension",
                    "plugin.js",
                ),
                (
                    "omp",
                    "models",
                    "--json",
                    "--config=overlay.yml",
                    "--extension",
                    "plugin.js",
                ),
            ),
            (
                ("omp", "--config", "overlay.yml"),
                ("omp", "models", "--json", "--config", "overlay.yml"),
            ),
            (
                (
                    "python3.12",
                    "-u",
                    "-m",
                    "native_launcher",
                    "--config",
                    "overlay.yml",
                    "--mode",
                    "rpc",
                ),
                (
                    "python3.12",
                    "-u",
                    "-m",
                    "native_launcher",
                    "models",
                    "--json",
                    "--config",
                    "overlay.yml",
                ),
            ),
            (
                (
                    "bun",
                    "x",
                    "--bun",
                    "@oh-my-pi/pi-coding-agent",
                    "--extension",
                    "plugin.js",
                    "--mode=rpc",
                ),
                (
                    "bun",
                    "x",
                    "--bun",
                    "@oh-my-pi/pi-coding-agent",
                    "models",
                    "--json",
                    "--extension",
                    "plugin.js",
                ),
            ),
            (
                ("node", "native-launcher.js", "--no-extensions", "--mode", "rpc"),
                ("node", "native-launcher.js", "models", "--json", "--no-extensions"),
            ),
            (
                ("custom-omp", "--mode", "rpc", "--config", "overlay.yml"),
                ("custom-omp", "models", "--json", "--config", "overlay.yml"),
            ),
        ],
    )
    def test_options_follow_the_native_subcommand(self, command, expected) -> None:
        assert model_listing_command(command) == expected

    @pytest.mark.parametrize(
        "command",
        [
            (
                "custom-launcher",
                "--config",
                "launcher.yml",
                "native-target",
                "--mode",
                "rpc",
            ),
            (
                "bun",
                "--config",
                "bunfig.toml",
                "x",
                "@oh-my-pi/pi-coding-agent",
                "--mode",
                "rpc",
            ),
        ],
    )
    def test_ambiguous_launcher_configuration_requires_native_rpc(
        self, command
    ) -> None:
        with pytest.raises(OmpListingUnavailable):
            model_listing_command(command)
