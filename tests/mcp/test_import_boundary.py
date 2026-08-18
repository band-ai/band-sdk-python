"""MCP-import boundary test (INT-1096 / INT-1150).

MCP-version isolation is a hard design constraint, not a posture: it's what
INT-1150 (the SDK's MCP Python SDK v2 migration, sequenced right after this
consolidation) requires -- "MCP-facing imports are confined to explicit
integration/transport modules" and "the framework-neutral engine does not
expose MCPServer, transport-security, or wire-model types." Making it a real
test now means the v2 migration only has to touch the allowlisted modules
below, not audit the whole tree for stray ``mcp``-package imports.

This scans real source files for ``import mcp`` / ``from mcp...`` at module
level -- no import-time side effects, no needing every extra installed.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.paths import REPO_ROOT

# The only places an `mcp`-package import may appear.
#
# src/band/runtime/mcp_server.py is NOT on this list: it's now a pure
# re-export shim (see that module) with no mcp-package import of its own.
# packages/band-mcp/src/band_mcp/tools/registrar.py is NOT on this list
# either: deleted in step 11, fully absorbed into engine.py.
_ALLOWED_MCP_IMPORT_FILES: frozenset[Path] = frozenset(
    REPO_ROOT / path
    for path in (
        "src/band/integrations/mcp/engine.py",
        "src/band/integrations/mcp/local_server.py",
        "src/band/integrations/desktop_app/server.py",
        "packages/band-mcp/src/band_mcp/shared.py",
        "packages/band-mcp/src/band_mcp/server.py",
    )
)

_SCAN_ROOTS = (REPO_ROOT / "src" / "band", REPO_ROOT / "packages" / "band-mcp" / "src")


def _imports_mcp_package(source: str) -> bool:
    """True if ``source`` has a module-level import of the ``mcp`` package
    (not a same-named local module -- checked by exact top-level component)."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "mcp" for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "mcp":
                return True
    return False


def test_mcp_package_imports_are_confined_to_the_allowlist() -> None:
    offenders: list[Path] = []
    for scan_root in _SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            if path in _ALLOWED_MCP_IMPORT_FILES:
                continue
            if _imports_mcp_package(path.read_text()):
                offenders.append(path.relative_to(REPO_ROOT))

    assert not offenders, (
        "Found mcp-package imports outside the INT-1096/INT-1150 allowlist: "
        f"{sorted(str(p) for p in offenders)}. Either this file belongs on the "
        "allowlist (update _ALLOWED_MCP_IMPORT_FILES with why), or the import "
        "needs to move into an allowlisted transport/translation module."
    )


def test_allowlist_entries_still_exist() -> None:
    """Catch a stale allowlist entry (a file the plan says should be deleted
    by a given step, but the deletion never landed -- or a typo'd path)."""
    missing = [
        path.relative_to(REPO_ROOT)
        for path in _ALLOWED_MCP_IMPORT_FILES
        if not path.is_file()
    ]
    assert not missing, f"Allowlisted paths no longer exist: {missing}"
