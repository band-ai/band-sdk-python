"""Letta lane placeholder.

Letta has no live E2E yet: running it needs a band-mcp server reachable from the
Letta server (whose SSRF guard rejects a loopback MCP URL), which isn't wired. The
adapter is registered ``e2e_pending`` so the ``letta`` CI lane stays defined but
runs no adapter cells; this single passing test stands in until the real Letta
smokes land in a follow-up.
"""

from __future__ import annotations

from tests.e2e.baseline.toolkit.adapters import Adapter, ci_lanes, spec_for


def test_letta_lane_pending_placeholder() -> None:
    """Letta is registered e2e_pending and still owns the ``letta`` CI lane."""
    # TODO: Replace this placeholder with real Letta E2E smokes (recall, room
    # isolation) once a band-mcp server reachable from the Letta server is wired
    # up, and drop e2e_pending from the Letta registry entry in toolkit/adapters.py.
    assert spec_for(Adapter.LETTA).e2e_pending is True
    assert "letta" in {str(lane.id) for lane in ci_lanes()}
