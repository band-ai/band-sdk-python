from __future__ import annotations


def test_band_import_surface_exposes_agent_and_link() -> None:
    from band import Agent, BandLink

    assert Agent.__name__ == "Agent"
    assert BandLink.__name__ == "BandLink"


def test_thenvoi_import_surface_keeps_compatibility_aliases() -> None:
    from thenvoi import BandLink, ThenvoiLink

    assert ThenvoiLink is BandLink
