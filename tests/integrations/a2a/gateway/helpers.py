"""Shared test-data builders for the A2A gateway."""

from __future__ import annotations

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
