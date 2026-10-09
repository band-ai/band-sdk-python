"""How a custom tool's input model looks to Parlant.

Parlant advertises each tool parameter with its own descriptor but casts the
engine's string arguments by the function signature, with a cast that is
unsafe for typed values (``"False"`` becomes ``True``, and a failure reaches
the model only as a bare "Tool call error"). So each field is advertised with
its model-derived type while its signature stays ``str``, leaving every
conversion to the tool itself, where a failure is a readable tool result.

No Parlant import, so an adapter can check its custom tools in any venv.
"""

from __future__ import annotations

import ast
import inspect
import json
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, NamedTuple, TypedDict

from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic.fields import FieldInfo

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
    annotations = _field_annotations(input_model)
    fields = [
        _field(
            key,
            prop,
            annotation=annotations.get(key),
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
    annotation: Any,
    defs: Mapping[str, Any],
    required: bool,
    where: str,
) -> CustomToolField:
    if key == CONTEXT_PARAMETER:
        raise ValueError(f"{where}: '{key}' is reserved for Parlant's tool context")
    resolved = _resolve(prop, defs=defs)
    descriptor = _descriptor(resolved, defs=defs, where=where)
    if description := prop.get("description") or resolved.get("description"):
        descriptor["description"] = description
    if annotation is not None and any(
        _rejects(annotation, value=choice) for choice in _delivered_choices(descriptor)
    ):
        raise ValueError(
            f"{where}: Parlant delivers its choices as strings, which the field "
            "rejects (use a str Literal, a str Enum or an IntEnum)"
        )
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


def _field_annotations(input_model: type[BaseModel]) -> dict[str, Any]:
    """Each field's type, keyed as its JSON schema property."""
    return {
        _schema_key(name, info): info.annotation
        for name, info in input_model.model_fields.items()
    }


def _schema_key(name: str, info: FieldInfo) -> str:
    if isinstance(info.validation_alias, str):
        return info.validation_alias
    return info.alias or name


def _delivered_choices(descriptor: Descriptor) -> list[Any]:
    """Each value Parlant can deliver from the advertised choices."""
    choices = descriptor.get("enum", [])
    if descriptor["type"] == ParlantType.ARRAY:
        return [[choice] for choice in choices]
    return list(choices)


def _rejects(annotation: Any, *, value: Any) -> bool:
    """Whether the field's bare type fails *value*.

    Only the type is checked: the field's constraints and the model's
    validators may need the rest of a call's input.
    """
    try:
        TypeAdapter(annotation).validate_python(value, strict=STRICT_VALIDATION)
    except ValidationError:
        return True
    return False


def _resolve(prop: Mapping[str, Any], *, defs: Mapping[str, Any]) -> Mapping[str, Any]:
    """*prop* with ``$ref`` and ``Optional`` unwrapped."""
    if ref := prop.get("$ref"):
        return _resolve(defs[ref.rsplit("/", 1)[-1]], defs=defs)
    branches = [branch for branch in prop.get("anyOf", ()) if branch != NULL_SCHEMA]
    if len(branches) == 1:
        return _resolve(branches[0], defs=defs)
    return prop


def _descriptor(
    resolved: Mapping[str, Any], *, defs: Mapping[str, Any], where: str
) -> Descriptor:
    """The Parlant parameter descriptor for one resolved property schema."""
    match resolved:
        case {"const": value}:
            # A single-value Literal; offered like any other set of choices.
            return _descriptor({"enum": [value]}, defs=defs, where=where)
        case {"enum": values}:
            # A None choice is how Parlant omits the argument, not a string.
            return Descriptor(
                type=ParlantType.STRING,
                enum=[str(value) for value in values if value is not None],
            )
        case {"type": JsonType.STRING, "format": str(fmt)} if fmt in FORMAT_TYPES:
            return Descriptor(type=FORMAT_TYPES[fmt])
        case {"type": str(json_type)} if json_type in SCALAR_TYPES:
            return Descriptor(type=SCALAR_TYPES[JsonType(json_type)])
        case {"type": JsonType.ARRAY, "items": items}:
            return _array_descriptor(_resolve(items, defs=defs), defs=defs, where=where)
        case {"type": JsonType.ARRAY}:
            raise ValueError(f"{where}: Parlant lists need one item type (no tuples)")
        case {"anyOf": branches}:
            return _union_descriptor(branches, defs=defs, where=where)
        case _:
            raise ValueError(
                f"{where}: Parlant has no object parameter type (dicts, nested models)"
            )


def _union_descriptor(
    branches: list[Mapping[str, Any]], *, defs: Mapping[str, Any], where: str
) -> Descriptor:
    """A multi-type union (or Decimal) arrives as text the model validates,
    which only works when every member is a scalar."""
    for branch in branches:
        if branch == NULL_SCHEMA:
            continue
        member = _descriptor(_resolve(branch, defs=defs), defs=defs, where=where)
        if member["type"] == ParlantType.ARRAY:
            raise ValueError(f"{where}: Parlant has no type for a union with a list")
    return Descriptor(type=ParlantType.STRING)


def _array_descriptor(
    item: Mapping[str, Any], *, defs: Mapping[str, Any], where: str
) -> Descriptor:
    item_descriptor = _descriptor(item, defs=defs, where=f"{where} item")
    if item_descriptor["type"] == ParlantType.ARRAY:
        raise ValueError(f"{where}: Parlant has no nested list parameter type")
    descriptor = Descriptor(type=ParlantType.ARRAY, item_type=item_descriptor["type"])
    if "enum" in item_descriptor:
        descriptor["enum"] = item_descriptor["enum"]
    return descriptor
