"""Wire-schema snapshot test for the published ``band-mcp`` contract.

Locks in the parts of band-mcp's advertised tool schemas a real client's
calls depend on, so an accidental wire-contract change (field rename,
dropped alias, narrowed enum/type/length) fails loudly here instead of
silently shipping. Real MCP protocol round trip via the SDK's in-memory
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
from typing import Any

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from band.integrations.mcp.engine import build_engine
from band_mcp.config import Config
from band_mcp.server import standalone_spec
from band_mcp.shared import build_standalone_resolver
from tests.mcp.conftest import advertised_schemas

logger = logging.getLogger(__name__)

# The JSON Schema keys that decide whether a real call is accepted or
# rejected. Everything else (title, description, ...) is prose: free to
# reword without breaking a client, so it's excluded from the snapshot.
_LOAD_BEARING_KEYS = frozenset(
    {"type", "enum", "items", "maxLength", "minLength", "additionalProperties"}
)


def _resolve_type_shape(value: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a property schema to its load-bearing shape, following refs.

    A property is either inline, a ``$ref`` into the schema's own
    ``$defs`` (Pydantic's rendering of a nested enum/model type), or an
    ``anyOf`` of either (an ``X | None`` field) -- resolve all three to the
    same shape so a ref'd enum's allowed values are covered exactly like an
    inline one.
    """
    if "$ref" in value:
        def_name = value["$ref"].rsplit("/", 1)[-1]
        return _resolve_type_shape(defs[def_name], defs)
    if "anyOf" in value:
        return {"anyOf": [_resolve_type_shape(v, defs) for v in value["anyOf"]]}
    shape = {key: value[key] for key in _LOAD_BEARING_KEYS if key in value}
    if "items" in shape:
        shape["items"] = _resolve_type_shape(shape["items"], defs)
    return shape


def _load_bearing_shapes(
    schemas: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Project each tool's full advertised schema down to its wire contract:
    which parameters exist, which are required, and what values each accepts."""
    shapes: dict[str, dict[str, Any]] = {}
    for name, entry in schemas.items():
        input_schema = entry["inputSchema"]
        defs = input_schema.get("$defs", {})
        properties = {
            field_name: _resolve_type_shape(field_schema, defs)
            for field_name, field_schema in input_schema.get("properties", {}).items()
        }
        shapes[name] = {
            "required": sorted(input_schema.get("required", [])),
            "properties": properties,
        }
    return shapes


SNAPSHOT_DIR = Path(__file__).parent.parent / "fixtures" / "wire_schemas"

# "full": every agent+human tool, contacts+memory opted in, unpinned --
# the broadest published surface. "pinned": the CLI's --room-id mode, which
# hides chat_id from the advertised schema entirely.
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
        return _load_bearing_shapes(await advertised_schemas(session))


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
