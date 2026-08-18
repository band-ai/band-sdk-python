"""Wire-schema snapshot test for the published ``band-mcp`` contract.

Locks in band-mcp's advertised tool schemas so any accidental wire-contract
change (field rename, dropped alias, schema shape) fails loudly here instead
of silently shipping. Real MCP protocol round trip via the SDK's in-memory
transport -- no patching, no hand-rolled stubs.

To regenerate after an *intentional* contract change, review the diff and
run (module form -- the script imports the ``tests`` package):
    uv run --all-packages python -m tests.mcp.test_wire_schema_snapshot
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import build_engine
from band_mcp.config import Config
from band_mcp.server import standalone_spec
from band_mcp.shared import build_standalone_resolver
from tests.mcp.conftest import advertised_schemas

logger = logging.getLogger(__name__)

SNAPSHOT_DIR = Path(__file__).parent.parent / "fixtures" / "wire_schemas"

# "full": every agent+human tool, contacts+memory opted in, unpinned --
# the broadest published surface. "pinned": the CLI's --room-id mode, which
# hides chat_id from the advertised schema entirely (divergence-matrix row 3).
_PROFILES: dict[str, Config] = {
    "full": Config(scope=["agent", "human"], tools=["contacts", "memory"]),
    "pinned": Config(scope=["agent"], tools=[], room_id="r_pinned_snapshot"),
}


def _build_mcp(config: Config) -> FastMCP:
    resolver = build_standalone_resolver(config)
    return build_engine(standalone_spec(config, resolver))


async def _current_schemas(profile: str) -> dict[str, dict[str, object]]:
    mcp = _build_mcp(_PROFILES[profile])
    async with create_connected_server_and_client_session(mcp) as session:
        return await advertised_schemas(session)


def _snapshot_path(profile: str) -> Path:
    return SNAPSHOT_DIR / f"{profile}.json"


@pytest.mark.parametrize("profile", sorted(_PROFILES))
async def test_advertised_schema_matches_snapshot(profile: str) -> None:
    current = await _current_schemas(profile)
    checked_in = json.loads(_snapshot_path(profile).read_text())
    assert current == checked_in, (
        f"band-mcp's advertised '{profile}' schema drifted from the checked-in "
        f"snapshot at {_snapshot_path(profile)}. If this is an *intentional* "
        "wire-contract change, regenerate with "
        "`uv run --all-packages python tests/mcp/test_wire_schema_snapshot.py` "
        "and review the diff."
    )


async def _generate_all() -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    for profile in _PROFILES:
        schemas = await _current_schemas(profile)
        _snapshot_path(profile).write_text(
            json.dumps(schemas, indent=2, sort_keys=True) + "\n"
        )
        logger.info("wrote %s (%d tools)", _snapshot_path(profile), len(schemas))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_generate_all())
