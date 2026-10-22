"""Profile-neutral JSON Schema constructors; no registry or version imports."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any


def ref(name: str) -> dict[str, Any]:
    return {"$ref": "#/$defs/" + name}


def obj(fields: dict[str, Any], optional: Collection[str] = ()) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": fields,
        "required": [key for key in fields if key not in optional],
        "additionalProperties": False,
    }


def array(
    item: dict[str, Any], minimum: int = 0, unique: bool = False
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": item,
        "minItems": minimum,
        **({"uniqueItems": True} if unique else {}),
    }


def mapping(item: dict[str, Any], keys: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": item,
        **({"propertyNames": keys} if keys else {}),
    }


def nullable(value: Any) -> dict[str, Any]:
    return {"anyOf": [{"type": "null"}, value]}


def enum(*values: Any) -> dict[str, Any]:
    return {"enum": list(values)}


def const(value: Any) -> dict[str, Any]:
    return {"const": value}
