"""Only ``band.integrations.mcp`` starts Band MCP servers.

A new adapter goes through ``SharedBandMCPBackend``, which starts, replaces
and stops its server; hand-rolling any of that is how a crash goes unseen.
"""

from __future__ import annotations

import ast

import pytest

from tests.paths import REPO_ROOT, SHIPPED_SOURCE_ROOTS, SRC_ROOT

_OWNER_PACKAGE = SRC_ROOT / "integrations" / "mcp"


def _runs_a_backend_by_hand(source: str) -> bool:
    """Whether ``source`` creates or constructs a Band MCP server itself."""
    for node in ast.walk(ast.parse(source)):
        match node:
            case (
                ast.Name(id="create_band_mcp_backend")
                | ast.Attribute(attr="create_band_mcp_backend")
                | ast.alias(name="create_band_mcp_backend")
            ):
                return True
            case (
                ast.Call(
                    func=ast.Name(id="LocalMCPServer")
                    | ast.Attribute(attr="LocalMCPServer")
                )
                | ast.alias(name="LocalMCPServer", asname=str())
            ):
                return True
    return False


def test_only_the_owner_runs_band_mcp_backends() -> None:
    assert all(root.is_dir() for root in (*SHIPPED_SOURCE_ROOTS, _OWNER_PACKAGE))
    offenders = sorted(
        path.relative_to(REPO_ROOT)
        for root in SHIPPED_SOURCE_ROOTS
        for path in root.rglob("*.py")
        if not path.is_relative_to(_OWNER_PACKAGE)
        and _runs_a_backend_by_hand(path.read_text(encoding="utf-8"))
    )

    assert not offenders, (
        f"{[str(path) for path in offenders]} run a Band MCP backend by hand; "
        "hold a SharedBandMCPBackend and declare BandMCPBackendSettings instead."
    )


@pytest.mark.parametrize(
    ("source", "by_hand"),
    [
        ("from band.integrations.mcp.backends import create_band_mcp_backend", True),
        ("backend = await backends.create_band_mcp_backend(settings)", True),
        ("server = LocalMCPServer(name='band', tool_registrations=[])", True),
        ("server = local_server.LocalMCPServer(name='band')", True),
        ("from band.runtime.mcp_server import LocalMCPServer as Server", True),
        ("from band.runtime.mcp_server import LocalMCPServer", False),
        ("backend = await self._mcp.ensure()", False),
    ],
)
def test_the_guard_recognizes_a_hand_rolled_backend(source: str, by_hand: bool) -> None:
    assert _runs_a_backend_by_hand(source) is by_hand
