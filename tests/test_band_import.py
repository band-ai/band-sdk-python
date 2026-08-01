from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap

import pytest


def test_band_import_surface_exposes_agent_and_link() -> None:
    from band import (
        Agent,
        BandLink,
        LogLevel,
        LoggingConfig,
        LoggingStyle,
        LogStream,
        build_logging_config,
        configure_logging,
    )

    assert Agent.__name__ == "Agent"
    assert BandLink.__name__ == "BandLink"
    assert LogLevel is not None
    assert LoggingConfig is not None
    assert LoggingStyle is not None
    assert LogStream is not None
    assert build_logging_config.__name__ == "build_logging_config"
    assert configure_logging.__name__ == "configure_logging"


def test_legacy_root_package_is_not_available() -> None:
    # The SDK package is `band`; the bare legacy root must not ship in-tree.
    # `band_rest` / `thenvoi_testing` are legitimate external pip
    # dependencies (the Fern-generated REST client and test tooling), so they
    # are intentionally importable.
    legacy_root = "then" + "voi"

    assert importlib.util.find_spec(legacy_root) is None


def test_band_submodule_imports_use_band_modules() -> None:
    import band.adapters
    import band.integrations.acp

    assert band.adapters.__name__ == "band.adapters"
    assert band.integrations.acp.__name__ == "band.integrations.acp"


def test_acp_facades_expose_band_names_only() -> None:
    import band.adapters as adapters
    import band.integrations.acp as acp
    from band.adapters import BandACPServerAdapter as BandAdapterFacade
    from band.integrations.acp import BandACPClient, BandACPServerAdapter

    legacy_prefix = "Then" + "voi"

    assert BandAdapterFacade is BandACPServerAdapter
    assert not BandACPClient.__name__.startswith(legacy_prefix)
    assert not hasattr(adapters, f"{legacy_prefix}ACPServerAdapter")
    assert not hasattr(acp, f"{legacy_prefix}ACPClient")
    assert not hasattr(acp, f"{legacy_prefix}ACPServerAdapter")


@pytest.mark.parametrize(
    ("adapter_module", "blocked_sdk"),
    [
        ("band.adapters.anthropic", "google"),
        ("band.adapters.gemini", "anthropic"),
    ],
)
def test_an_adapter_imports_without_a_sibling_provider_sdk(
    adapter_module: str, blocked_sdk: str
) -> None:
    """Installing one provider extra must be enough to use that provider.

    Each provider module imports its vendor SDK at module scope, so anything
    naming them all eagerly turns every extra into a hard dependency of the
    others. CI installs every extra, so only an environment that is actually
    missing one can catch that — hence the subprocess with the sibling SDK
    made unimportable.
    """
    program = textwrap.dedent(f"""
        import sys

        class BlockSdk:
            def find_spec(self, name, path=None, target=None):
                if name == {blocked_sdk!r} or name.startswith({blocked_sdk!r} + "."):
                    raise ImportError("not installed: " + name)
                return None

        sys.meta_path.insert(0, BlockSdk())
        import {adapter_module}
    """)

    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True
    )

    assert result.returncode == 0, (
        f"{adapter_module} needs {blocked_sdk} installed:\n{result.stderr}"
    )


def test_mcp_facade_exposes_band_backend_names_only() -> None:
    import band.integrations.mcp as mcp
    from band.integrations.mcp import BandMCPBackend, BandMCPBackendKind

    legacy_prefix = "Then" + "voi"

    assert BandMCPBackend.__name__ == "BandMCPBackend"
    assert BandMCPBackendKind.__name__ == "BandMCPBackendKind"
    assert not hasattr(mcp, f"{legacy_prefix}MCPBackend")
    assert not hasattr(mcp, f"{legacy_prefix}MCPBackendKind")
