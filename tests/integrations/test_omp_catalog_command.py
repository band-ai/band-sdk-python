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
            (
                ("omp", "launch", "--mode", "rpc"),
                ("omp", "models", "--json"),
            ),
            (
                ("omp", "launch", "--config", "launch", "--mode=rpc"),
                ("omp", "models", "--json", "--config", "launch"),
            ),
            (
                (
                    "python3.12",
                    "-u",
                    "-m",
                    "native_launcher",
                    "launch",
                    "--mode",
                    "rpc",
                ),
                ("python3.12", "-u", "-m", "native_launcher", "models", "--json"),
            ),
            (
                (
                    "bun",
                    "x",
                    "--bun",
                    "@oh-my-pi/pi-coding-agent",
                    "launch",
                    "--mode=rpc",
                ),
                ("bun", "x", "--bun", "@oh-my-pi/pi-coding-agent", "models", "--json"),
            ),
            (
                ("node", "native-launcher.js", "launch", "--mode", "rpc"),
                ("node", "native-launcher.js", "models", "--json"),
            ),
            (
                ("python3.12", "-m", "launch", "--mode", "rpc"),
                ("python3.12", "-m", "launch", "models", "--json"),
            ),
            (
                ("bun", "run", "launch", "--mode", "rpc"),
                ("bun", "run", "launch", "models", "--json"),
            ),
            (
                ("node", "launch", "--mode", "rpc"),
                ("node", "launch", "models", "--json"),
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
            ("omp", "--config", "overlay.yml", "launch", "--mode", "rpc"),
            ("custom-omp", "launch", "--mode", "rpc"),
            ("omp", "--config", "launch", "--mode", "rpc"),
        ],
    )
    def test_ambiguous_launcher_configuration_requires_native_rpc(
        self, command
    ) -> None:
        with pytest.raises(OmpListingUnavailable):
            model_listing_command(command)
