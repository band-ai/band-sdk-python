from __future__ import annotations

import importlib.util


def test_band_import_surface_exposes_agent_and_link() -> None:
    from band import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        Agent,
        BandLink,
        LoggingConfig,
        LoggingStyle,
        LogLevel,
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
    import band.adapters  # noqa: PLC0415 -- pins the exact import path this test exercises
    import band.integrations.acp  # noqa: PLC0415 -- pins the exact import path this test exercises

    assert band.adapters.__name__ == "band.adapters"
    assert band.integrations.acp.__name__ == "band.integrations.acp"


def test_acp_facades_expose_band_names_only() -> None:
    from band import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        adapters,
    )
    from band.adapters import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        BandACPServerAdapter as BandAdapterFacade,
    )
    from band.integrations import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        acp,
    )
    from band.integrations.acp import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        BandACPClient,
        BandACPServerAdapter,
    )

    legacy_prefix = "Then" + "voi"

    assert BandAdapterFacade is BandACPServerAdapter
    assert BandACPClient.__name__ == "BandACPClient"
    assert not hasattr(adapters, f"{legacy_prefix}ACPServerAdapter")
    assert not hasattr(acp, f"{legacy_prefix}ACPClient")
    assert not hasattr(acp, f"{legacy_prefix}ACPServerAdapter")


def test_mcp_facade_exposes_band_backend_names_only() -> None:
    from band.integrations import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        mcp,
    )
    from band.integrations.mcp import (  # noqa: PLC0415 -- pins the exact import path this test exercises
        BandMCPBackend,
        BandMCPBackendKind,
    )

    legacy_prefix = "Then" + "voi"

    assert BandMCPBackend.__name__ == "BandMCPBackend"
    assert BandMCPBackendKind.__name__ == "BandMCPBackendKind"
    assert not hasattr(mcp, f"{legacy_prefix}MCPBackend")
    assert not hasattr(mcp, f"{legacy_prefix}MCPBackendKind")
