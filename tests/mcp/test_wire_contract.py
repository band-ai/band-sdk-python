"""Wire-contract tests for the published ``band-mcp`` tool schemas.

Drives a real MCP `tools/list` round trip (real ``build_engine`` +
``standalone_spec``, real in-memory MCP transport, no mocking) and checks
the result against small, hand-written, declarative contracts -- not a
diff against a checked-in JSON blob. Only tools with a genuinely
non-obvious wire invariant (an enum, an array item type, a required-set
that isn't just "every field") get a contract entry; a plain string/int/
bool CRUD field has nothing here to drift, so it isn't asserted on. Enum
values are read from the real ``StrEnum``/``Literal`` they come from, not
copied by hand, so this can't silently go stale when a value is added or
removed there.

The ``chat_id`` room-binding behavior (required when unpinned, hidden
entirely when pinned) is checked once, generically, against every tool in
``AGENT_ROOM_BOUND_TOOL_NAMES`` -- the same set the engine itself uses --
rather than repeated per tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, get_args, get_origin

from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from pydantic import BaseModel

from band.core.memory_types import (
    MemoryListScope,
    MemorySegment,
    MemoryStatus,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
    enum_values,
)
from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState
from band.integrations.mcp.engine import WideEventMessageType, build_engine
from band.runtime.tools import (
    AGENT_ROOM_BOUND_TOOL_NAMES,
    CHAT_ID_FIELD_NAME,
    FILE_TOOL_NAMES,
    AddParticipantInput,
    GetBoardInput,
    GetTaskInput,
    ListContactRequestsInput,
    ListSentContactRequestsInput,
    RespondContactRequestInput,
)
from band_mcp.config import Config, Scope, ToolGroup
from band_mcp.server import standalone_spec
from band_mcp.shared import build_standalone_resolver
from tests.mcp.conftest import advertised_schemas

# The JSON Schema keys that decide whether a real call is accepted or
# rejected. Everything else (title, description, ...) is prose: free to
# reword without breaking a client, so it's excluded from the comparison.
_LOAD_BEARING_KEYS = frozenset(
    {"type", "enum", "items", "maxLength", "minLength", "additionalProperties"}
)


def _resolve_type_shape(value: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """Resolve a property schema to its load-bearing shape, following refs.

    A property is either inline, a ``$ref`` into the schema's own
    ``$defs`` (Pydantic's rendering of a nested enum/model type), or an
    ``anyOf`` of either (an ``X | None`` field, or two enums merged) --
    resolve all three to the same shape so a ref'd enum's allowed values
    are covered exactly like an inline one.
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


# "full": every agent+human tool, contacts+memory opted in, unpinned --
# the broadest published surface. "pinned": the CLI's --room-id mode, which
# hides chat_id from the advertised schema entirely.
_PROFILES: dict[str, Config] = {
    "full": Config(
        scope=[Scope.AGENT, Scope.HUMAN],
        tools=[ToolGroup.CONTACTS, ToolGroup.MEMORY, ToolGroup.TASKS],
    ),
    "pinned": Config(
        scope=[Scope.AGENT], tools=[ToolGroup.TASKS], room_id="r_pinned_snapshot"
    ),
}

# band-mcp's `--tools` vocabulary has no `files` group yet (Capability.FILES
# has no platform-negotiation moment in this CLI's synchronous startup), so
# these room-bound tools are never reachable through it even on the "full"
# profile -- excluded from both room-binding checks below rather than
# asserted unreachable.
_ROOM_BOUND_TOOL_NAMES_IN_BAND_MCP = AGENT_ROOM_BOUND_TOOL_NAMES - FILE_TOOL_NAMES


def _build_mcp(config: Config) -> FastMCP:
    resolver = build_standalone_resolver(config)
    return build_engine(standalone_spec(config, resolver))


async def _current_schemas(profile: str) -> dict[str, dict[str, Any]]:
    mcp = _build_mcp(_PROFILES[profile])
    async with create_connected_server_and_client_session(mcp) as session:
        return _load_bearing_shapes(await advertised_schemas(session))


def _field_literal_values(model: type[BaseModel], field_name: str) -> tuple[str, ...]:
    """The allowed values of a field typed as a bare ``Literal`` (optionally
    ``Literal[...] | None``), reflected off the model itself so this test
    can't silently drift from the validator's own source of truth."""
    annotation = model.model_fields[field_name].annotation
    for candidate in (annotation, *get_args(annotation)):
        if get_origin(candidate) is Literal:
            return get_args(candidate)
    raise TypeError(
        f"{model.__name__}.{field_name} is not a Literal field: {annotation}"
    )


_MEMORY_TYPE_VALUES = enum_values(MemoryType)


@dataclass(frozen=True)
class FieldContract:
    """The load-bearing shape of one property: the part a real call's
    acceptance depends on."""

    type: str
    enum: tuple[str, ...] = ()
    item_type: str | None = None
    nullable: bool = False


@dataclass(frozen=True)
class ToolContract:
    """A tool's wire contract, excluding ``chat_id`` -- room-binding is
    checked generically via ``AGENT_ROOM_BOUND_TOOL_NAMES`` instead."""

    required: frozenset[str] = frozenset()
    fields: Mapping[str, FieldContract] = field(default_factory=dict)


# Only tools with a real, non-obvious wire invariant. A plain required
# string/int/bool field has nothing here worth pinning.
CONTRACTS: dict[str, ToolContract] = {
    "band_send_message": ToolContract(
        required=frozenset({"content", "mentions"}),
        fields={"mentions": FieldContract(type="array", item_type="string")},
    ),
    "band_send_event": ToolContract(
        required=frozenset({"content", "message_type"}),
        fields={
            "message_type": FieldContract(
                type="string", enum=get_args(WideEventMessageType)
            )
        },
    ),
    "band_add_participant": ToolContract(
        required=frozenset({"identifier"}),
        fields={
            "role": FieldContract(
                type="string",
                enum=_field_literal_values(AddParticipantInput, "role"),
            )
        },
    ),
    "band_remove_participant": ToolContract(required=frozenset({"identifier"})),
    "band_store_memory": ToolContract(
        required=frozenset(
            {"content", "scope", "segment", "system", "thought", "type"}
        ),
        fields={
            "scope": FieldContract(type="string", enum=enum_values(MemoryStoreScope)),
            "segment": FieldContract(type="string", enum=enum_values(MemorySegment)),
            "system": FieldContract(type="string", enum=enum_values(MemorySystem)),
            "type": FieldContract(type="string", enum=_MEMORY_TYPE_VALUES),
        },
    ),
    "band_list_memories": ToolContract(
        fields={
            "scope": FieldContract(
                type="string", enum=enum_values(MemoryListScope), nullable=True
            ),
            "segment": FieldContract(
                type="string", enum=enum_values(MemorySegment), nullable=True
            ),
            "status": FieldContract(
                type="string", enum=enum_values(MemoryStatus), nullable=True
            ),
            "system": FieldContract(
                type="string", enum=enum_values(MemorySystem), nullable=True
            ),
            "type": FieldContract(
                type="string", enum=_MEMORY_TYPE_VALUES, nullable=True
            ),
        },
    ),
    "band_respond_contact_request": ToolContract(
        required=frozenset({"action"}),
        fields={
            "action": FieldContract(
                type="string",
                enum=_field_literal_values(RespondContactRequestInput, "action"),
            )
        },
    ),
    "band_list_contact_requests": ToolContract(
        fields={
            "sent_status": FieldContract(
                type="string",
                enum=_field_literal_values(ListContactRequestsInput, "sent_status"),
            )
        },
    ),
    "band_list_sent_contact_requests": ToolContract(
        fields={
            "status": FieldContract(
                type="string",
                enum=_field_literal_values(ListSentContactRequestsInput, "status"),
                nullable=True,
            )
        },
    ),
    "band_list_tasks": ToolContract(
        fields={
            "state": FieldContract(
                type="string", enum=enum_values(TaskListState), nullable=True
            )
        },
    ),
    "band_create_task": ToolContract(required=frozenset({"subject"})),
    "band_get_task": ToolContract(
        required=frozenset({"id"}),
        fields={
            "include": FieldContract(
                type="string",
                enum=_field_literal_values(GetTaskInput, "include"),
                nullable=True,
            )
        },
    ),
    "band_update_task": ToolContract(
        required=frozenset({"id"}),
        fields={
            "status": FieldContract(
                type="string", enum=enum_values(TaskAssignmentStatus), nullable=True
            ),
            "state": FieldContract(
                type="string", enum=enum_values(TaskLifecycleState), nullable=True
            ),
        },
    ),
    "band_get_task_history": ToolContract(required=frozenset({"id"})),
    "band_get_board": ToolContract(
        fields={
            "include": FieldContract(
                type="string",
                enum=_field_literal_values(GetBoardInput, "include"),
                nullable=True,
            )
        },
    ),
}


def _non_null_branches(shape: dict[str, Any]) -> list[dict[str, Any]]:
    """The shape's non-null ``anyOf`` alternatives, or itself if it isn't a union."""
    if "anyOf" in shape:
        return [branch for branch in shape["anyOf"] if branch.get("type") != "null"]
    return [shape]


def _is_nullable(shape: dict[str, Any]) -> bool:
    return "anyOf" in shape and any(b.get("type") == "null" for b in shape["anyOf"])


def _assert_field_matches(
    tool: str, field_name: str, actual: dict[str, Any], expected: FieldContract
) -> None:
    assert _is_nullable(actual) == expected.nullable, (
        f"{tool}.{field_name}: nullability drifted, got {actual!r}"
    )
    branches = _non_null_branches(actual)
    assert {b.get("type") for b in branches} == {expected.type}, (
        f"{tool}.{field_name}: type drifted, got {actual!r}"
    )
    if expected.enum:
        actual_enum = {value for b in branches for value in b.get("enum", ())}
        assert actual_enum == set(expected.enum), (
            f"{tool}.{field_name}: enum drifted, got {actual!r}, expected {expected.enum!r}"
        )
    if expected.item_type is not None:
        assert {b.get("items", {}).get("type") for b in branches} == {
            expected.item_type
        }, f"{tool}.{field_name}: array item type drifted, got {actual!r}"


def _assert_tool_matches(
    name: str,
    shape: dict[str, Any],
    contract: ToolContract,
    *,
    chat_id_expected: bool,
) -> None:
    if chat_id_expected:
        assert CHAT_ID_FIELD_NAME in shape["properties"], (
            f"{name}: chat_id missing from properties"
        )
    else:
        assert CHAT_ID_FIELD_NAME not in shape["properties"], (
            f"{name}: chat_id should be hidden (pinned mode)"
        )

    expected_required = set(contract.required)
    if chat_id_expected:
        expected_required.add(CHAT_ID_FIELD_NAME)
    assert set(shape["required"]) == expected_required, (
        f"{name}: required={shape['required']!r}, expected={sorted(expected_required)!r}"
    )

    for field_name, expected in contract.fields.items():
        assert field_name in shape["properties"], f"{name}.{field_name}: field missing"
        _assert_field_matches(
            name, field_name, shape["properties"][field_name], expected
        )


async def test_full_profile_matches_contract() -> None:
    """Every curated tool, plus every room-bound tool, advertises the shape
    its real Pydantic model/StrEnum sources define."""
    live = await _current_schemas("full")
    for name in sorted(_ROOM_BOUND_TOOL_NAMES_IN_BAND_MCP | CONTRACTS.keys()):
        _assert_tool_matches(
            name,
            live[name],
            CONTRACTS.get(name, ToolContract()),
            chat_id_expected=name in _ROOM_BOUND_TOOL_NAMES_IN_BAND_MCP,
        )


async def test_pinned_profile_hides_chat_id_and_matches_contract() -> None:
    """Pinned mode hides chat_id from every room-bound tool it advertises,
    without disturbing any other declared field."""
    live = await _current_schemas("pinned")
    for name in sorted(_ROOM_BOUND_TOOL_NAMES_IN_BAND_MCP):
        _assert_tool_matches(
            name,
            live[name],
            CONTRACTS.get(name, ToolContract()),
            chat_id_expected=False,
        )
