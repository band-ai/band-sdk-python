from __future__ import annotations


def test_band_import_surface_exposes_agent_and_link() -> None:
    from band import Agent, BandLink

    assert Agent.__name__ == "Agent"
    assert BandLink.__name__ == "BandLink"


def test_thenvoi_import_surface_keeps_compatibility_aliases() -> None:
    from thenvoi import BandLink, ThenvoiLink

    assert ThenvoiLink is BandLink


def test_band_submodule_imports_alias_thenvoi_modules() -> None:
    import band.adapters
    import thenvoi.adapters

    assert band.adapters is thenvoi.adapters


def test_acp_facades_expose_band_and_thenvoi_aliases() -> None:
    from band.adapters import BandACPServerAdapter as BandAdapterFacade
    from band.adapters import ThenvoiACPServerAdapter as ThenvoiAdapterFacade
    from band.integrations.acp import BandACPClient, BandACPServerAdapter
    from band.integrations.acp import ThenvoiACPClient, ThenvoiACPServerAdapter

    assert ThenvoiAdapterFacade is BandAdapterFacade
    assert ThenvoiACPServerAdapter is BandACPServerAdapter
    assert ThenvoiACPClient is BandACPClient


def test_mcp_facade_keeps_thenvoi_backend_aliases() -> None:
    from band.integrations.mcp import BandMCPBackend, ThenvoiMCPBackend
    from band.integrations.mcp import BandMCPBackendKind, ThenvoiMCPBackendKind

    assert ThenvoiMCPBackend is BandMCPBackend
    assert ThenvoiMCPBackendKind == BandMCPBackendKind
