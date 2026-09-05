"""Shared test-data builders for the A2A gateway."""

from __future__ import annotations

from types import SimpleNamespace

from band_rest import Peer


def make_peer(peer_id: str, name: str, description: str = "") -> Peer:
    """Build a representative registry peer for gateway tests."""
    return Peer(
        id=peer_id,
        name=name,
        type="Agent",
        description=description,
        handle=f"test/{name.lower().replace(' ', '-')}",
        is_contact=False,
        source="registry",
    )


def peers_page(peers: list[Peer]) -> SimpleNamespace:
    """A fake ``list_agent_peers`` response page -- only ``.data`` matters
    to ``_fetch_all_peers``, which pages until a page comes back short."""
    return SimpleNamespace(data=peers)
