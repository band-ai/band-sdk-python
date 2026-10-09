"""How a custom tool's input model looks to Parlant.

Parlant advertises each tool parameter with its own descriptor but casts the
engine's string arguments by the function signature, with a cast that is
unsafe for typed values (``"False"`` becomes ``True``, and a failure reaches
the model only as a bare "Tool call error"). So each field is advertised with
its model-derived type while its signature stays ``str``, leaving every
conversion to the tool itself, where a failure is a readable tool result.

The field shapes Parlant can carry, after ``$ref`` and ``Optional`` are
unwrapped; any other shape is rejected when the adapter is built:

- a scalar (``str`` in any format, ``int``, ``float``, ``bool``), advertised
  as its own type, or as ``date`` / ``datetime`` for those formats;
- string choices (a str ``Literal`` or a str-valued ``Enum``), advertised as
  ``string`` with an ``enum``: a string is what Parlant sends, so only a
  string value is sure to validate from it;
- a union of the shapes above (``int | str``, ``Decimal``), advertised as
  ``string``, with every member's choices when all members are choices;
- a list, set or ``tuple[X, ...]`` of one of those, advertised as ``array``
  with its item type and parsed from list text.

No Parlant import, so an adapter can check its custom tools in any venv.
"""

from __future__ import annotations

import ast
import inspect
import json
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
# Parlant delivers every argument as a string, which only lax validation
# converts, so even a strict field is validated laxly.
STRICT_VALIDATION = False
UNSUPPORTED_SHAPE = (
    "Parlant has no parameter type for this field; choices must be strings "
    "(supported shapes: band.integrations.parlant.customschema)"
)


class CustomToolField(NamedTuple):
    """One input-model field: the parameter Parlant casts by, and what it advertises."""

    parameter: inspect.Parameter
    descriptor: Descriptor


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
    if (descriptor := _descriptor(resolved, defs=defs)) is None:
        raise ValueError(f"{where}: {UNSUPPORTED_SHAPE}")
    if description := _description(prop, resolved, defs=defs):
        descriptor["description"] = description
    try:
        parameter = inspect.Parameter(
            key,
            inspect.Parameter.KEYWORD_ONLY,
            annotation=str,
            default=inspect.Parameter.empty if required else None,
        )
    except ValueError as exc:
        raise ValueError(f"{where}: not a valid Python parameter name") from exc
    return CustomToolField(parameter=parameter, descriptor=descriptor)


def parse_delivered(
    arguments: Mapping[str, Any], fields: Iterable[CustomToolField]
) -> dict[str, Any]:
    """*arguments* as delivered, with each list field's text parsed into a list."""
    lists = {
        field.parameter.name
        for field in fields
        if field.descriptor["type"] == ParlantType.ARRAY
    }
    return {
        name: _parsed_list(value) if name in lists and isinstance(value, str) else value
        for name, value in arguments.items()
    }


def _parsed_list(text: str) -> Any:
    """JSON, or the Python repr the engine's ``str()`` writes; anything else
    stays text, for the input model to reject readably."""
    for parse in (json.loads, ast.literal_eval):
        try:
            return parse(text)
        except (ValueError, SyntaxError, TypeError):
            continue
    return text


def _non_null(branches: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [branch for branch in branches if branch != NULL_SCHEMA]


def _resolve(prop: Mapping[str, Any], *, defs: Mapping[str, Any]) -> Mapping[str, Any]:
    """*prop* with ``$ref`` and ``Optional`` unwrapped."""
    if ref := prop.get("$ref"):
        return _resolve(defs[ref.rsplit("/", 1)[-1]], defs=defs)
    branches = _non_null(prop.get("anyOf", ()))
    if len(branches) == 1:
        return _resolve(branches[0], defs=defs)
    return prop


def _description(
    prop: Mapping[str, Any], resolved: Mapping[str, Any], *, defs: Mapping[str, Any]
) -> str | None:
    """The field's own description, else its type's, else its list items' type's."""
    items = _resolve(resolved.get("items", {}), defs=defs)
    return (
        prop.get("description")
        or resolved.get("description")
        or items.get("description")
    )


def _descriptor(
    schema: Mapping[str, Any], *, defs: Mapping[str, Any]
) -> Descriptor | None:
    """The Parlant descriptor for a field shape in the module's table, or ``None``."""
    match schema:
        case {"type": JsonType.ARRAY, "items": Mapping() as items}:
            item = _scalar_descriptor(_resolve(items, defs=defs), defs=defs)
            return None if item is None else _list_of(item)
        case _:
            return _scalar_descriptor(schema, defs=defs)


def _scalar_descriptor(
    schema: Mapping[str, Any], *, defs: Mapping[str, Any]
) -> Descriptor | None:
    """A single-value shape's descriptor, or ``None`` for any other shape."""
    match schema:
        case {"const": value}:
            return _choices([value])
        case {"enum": values}:
            return _choices(values)
        case {"type": JsonType.STRING, "format": str(fmt)} if fmt in FORMAT_TYPES:
            return Descriptor(type=FORMAT_TYPES[fmt])
        case {"type": str(json_type)} if json_type in SCALAR_TYPES:
            return Descriptor(type=SCALAR_TYPES[JsonType(json_type)])
        case {"anyOf": branches}:
            return _union(_non_null(branches), defs=defs)
        case _:
            return None


def _union(
    branches: Iterable[Mapping[str, Any]], *, defs: Mapping[str, Any]
) -> Descriptor | None:
    """Text the input model validates against each member in turn; a union of
    choices offers every member's choices."""
    members: list[Descriptor] = []
    for branch in branches:
        if (
            member := _scalar_descriptor(_resolve(branch, defs=defs), defs=defs)
        ) is None:
            return None
        members.append(member)
    if all("enum" in member for member in members):
        return _choices(choice for member in members for choice in member["enum"])
    return Descriptor(type=ParlantType.STRING)


def _choices(values: Iterable[Any]) -> Descriptor | None:
    """String choices; ``None`` when any choice is not a string."""
    # A None choice is how Parlant omits the argument, not a value it sends.
    sent = [value for value in values if value is not None]
    if not sent or not all(isinstance(value, str) for value in sent):
        return None
    return Descriptor(type=ParlantType.STRING, enum=list(dict.fromkeys(sent)))


def _list_of(item: Descriptor) -> Descriptor:
    descriptor = Descriptor(type=ParlantType.ARRAY, item_type=item["type"])
    if "enum" in item:
        descriptor["enum"] = item["enum"]
    return descriptor
