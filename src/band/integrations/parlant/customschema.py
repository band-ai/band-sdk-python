"""How a custom tool's input model looks to Parlant.

Parlant advertises each tool parameter with its own descriptor but casts the
engine's string arguments by the function signature, with a cast that is
unsafe for typed values (``"False"`` becomes ``True``, and a failure reaches
the model only as a bare "Tool call error"). So each field is advertised with
its model-derived type while its signature stays ``str`` / ``list[str]``,
leaving every conversion to the input model's own validation.

No Parlant import, so an adapter can check its custom tools in any venv.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, NamedTuple, TypedDict

from pydantic import BaseModel

from band.integrations.parlant.sessiontools import CONTEXT_PARAMETER
from band.runtime.custom_tools import (
    CustomToolDef,
    custom_tool_to_openai_schema,
    get_custom_tool_name,
    reject_conflicting_tool_names,
)


class ParlantType(StrEnum):
    """The Parlant parameter types a custom tool field is advertised as.

    A subset of Parlant's ``ToolParameterType`` (pinned by a parity test).
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    DATE = "date"
    DATETIME = "datetime"


class JsonType(StrEnum):
    """The JSON Schema ``type`` values pydantic writes for a field."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    NULL = "null"


class Descriptor(TypedDict, total=False):
    """Parlant's ``ToolParameterDescriptor`` shape (pinned by a parity test)."""

    type: ParlantType
    item_type: ParlantType
    enum: list[str]
    description: str


NULL_SCHEMA = {"type": JsonType.NULL}
SCALAR_TYPES = {
    JsonType.STRING: ParlantType.STRING,
    JsonType.INTEGER: ParlantType.INTEGER,
    JsonType.NUMBER: ParlantType.NUMBER,
    JsonType.BOOLEAN: ParlantType.BOOLEAN,
}
# JSON Schema string formats Parlant has a parameter type for.
FORMAT_TYPES = {"date": ParlantType.DATE, "date-time": ParlantType.DATETIME}


class CustomToolField(NamedTuple):
    """One input-model field: the parameter Parlant casts by, and what it advertises."""

    parameter: inspect.Parameter
    descriptor: Descriptor


class Resolved(NamedTuple):
    """A property schema with ``$ref`` and ``Optional`` unwrapped."""

    schema: Mapping[str, Any]
    # An enum reached through $defs is a real Enum class, whose member values
    # (even int ones) the model validates from their string form.
    from_enum_class: bool


class ParlantCustomTool(NamedTuple):
    """A custom tool as Parlant offers it: its name, description and fields."""

    name: str
    description: str
    fields: list[CustomToolField]


def check_parlant_custom_tools(tools: Iterable[CustomToolDef]) -> None:
    """Raise ``ValueError`` for a custom tool Parlant cannot offer."""
    models = [input_model for input_model, _ in tools]
    reject_conflicting_tool_names(get_custom_tool_name(model) for model in models)
    for model in models:
        describe_custom_tool(model)


def describe_custom_tool(input_model: type[BaseModel]) -> ParlantCustomTool:
    """*input_model* as the same function schema every other adapter offers."""
    function = custom_tool_to_openai_schema(input_model)["function"]
    schema = function["parameters"]
    defs = schema.get("$defs", {})
    required = set(schema.get("required", []))
    fields = [
        _field(
            key,
            prop,
            defs=defs,
            required=key in required,
            where=f"Custom tool '{function['name']}' field '{key}'",
        )
        for key, prop in schema.get("properties", {}).items()
    ]
    return ParlantCustomTool(
        name=function["name"], description=function["description"], fields=fields
    )


def _field(
    key: str,
    prop: Mapping[str, Any],
    *,
    defs: Mapping[str, Any],
    required: bool,
    where: str,
) -> CustomToolField:
    if key == CONTEXT_PARAMETER:
        raise ValueError(f"{where}: '{key}' is reserved for Parlant's tool context")
    resolved = _resolve(prop, defs=defs)
    descriptor = _descriptor(resolved, defs=defs, where=where)
    if description := prop.get("description") or resolved.schema.get("description"):
        descriptor["description"] = description
    delivered = list[str] if descriptor["type"] == ParlantType.ARRAY else str
    try:
        parameter = inspect.Parameter(
            key,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=delivered,
            default=inspect.Parameter.empty if required else None,
        )
    except ValueError as exc:
        raise ValueError(f"{where}: not a valid Python parameter name") from exc
    return CustomToolField(parameter=parameter, descriptor=descriptor)


def _resolve(prop: Mapping[str, Any], *, defs: Mapping[str, Any]) -> Resolved:
    if ref := prop.get("$ref"):
        return Resolved(schema=defs[ref.rsplit("/", 1)[-1]], from_enum_class=True)
    branches = [branch for branch in prop.get("anyOf", ()) if branch != NULL_SCHEMA]
    if len(branches) == 1:
        return _resolve(branches[0], defs=defs)
    return Resolved(schema=prop, from_enum_class=False)


def _descriptor(
    resolved: Resolved, *, defs: Mapping[str, Any], where: str
) -> Descriptor:
    """The Parlant parameter descriptor for one resolved property schema."""
    match resolved.schema:
        case {"const": value}:
            # A single-value Literal; checked like any other enum.
            return _descriptor(
                resolved._replace(schema={"enum": [value]}), defs=defs, where=where
            )
        case {"enum": values} if resolved.from_enum_class or all(
            isinstance(value, str) for value in values
        ):
            return Descriptor(
                type=ParlantType.STRING, enum=[str(value) for value in values]
            )
        case {"enum": _}:
            raise ValueError(
                f"{where}: Parlant enums must be strings (use a str Literal or an Enum)"
            )
        case {"type": JsonType.STRING, "format": str(fmt)} if fmt in FORMAT_TYPES:
            return Descriptor(type=FORMAT_TYPES[fmt])
        case {"type": str(json_type)} if json_type in SCALAR_TYPES:
            return Descriptor(type=SCALAR_TYPES[JsonType(json_type)])
        case {"type": JsonType.ARRAY, "items": items}:
            return _array_descriptor(_resolve(items, defs=defs), defs=defs, where=where)
        case {"type": JsonType.ARRAY}:
            raise ValueError(f"{where}: Parlant lists need one item type (no tuples)")
        case {"anyOf": _}:
            # A multi-type union (or Decimal) arrives as text the model validates.
            return Descriptor(type=ParlantType.STRING)
        case _:
            raise ValueError(
                f"{where}: Parlant has no object parameter type (dicts, nested models)"
            )


def _array_descriptor(
    item: Resolved, *, defs: Mapping[str, Any], where: str
) -> Descriptor:
    item_descriptor = _descriptor(item, defs=defs, where=f"{where} item")
    if item_descriptor["type"] == ParlantType.ARRAY:
        raise ValueError(f"{where}: Parlant has no nested list parameter type")
    descriptor = Descriptor(type=ParlantType.ARRAY, item_type=item_descriptor["type"])
    if "enum" in item_descriptor:
        descriptor["enum"] = item_descriptor["enum"]
    return descriptor
