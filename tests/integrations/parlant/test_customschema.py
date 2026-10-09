"""The field shapes a Parlant custom tool can carry: one row per shape.

``customschema``'s module docstring is the table these rows pin.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import pytest
from pydantic import BaseModel, create_model
from typing_extensions import TypeAliasType

from band.integrations.parlant.customschema import Descriptor, describe_custom_tool


class Shade(enum.Enum):
    LIGHT = "light"
    DARK = "dark"


class Grade(enum.IntEnum):
    ECONOMY = 1
    PREMIUM = 2


class Color(enum.Enum):
    RED = 1
    BLUE = 2


class Address(BaseModel):
    street: str


MaybeCount = TypeAliasType("MaybeCount", int | None)
MaybeTrays = TypeAliasType("MaybeTrays", list[int] | None)
Priority = TypeAliasType("Priority", Literal[1, 2])


def descriptor_of(annotation: Any) -> Descriptor:
    """The descriptor Parlant advertises for a lone optional field of *annotation*."""
    model = create_model("ShapeInput", field=(annotation, None))
    [field] = describe_custom_tool(model).fields
    return field.descriptor


@pytest.mark.parametrize(
    ("annotation", "advertised"),
    [
        (str, {"type": "string"}),
        (UUID, {"type": "string"}),
        (int, {"type": "integer"}),
        (float, {"type": "number"}),
        (bool, {"type": "boolean"}),
        (date, {"type": "date"}),
        (datetime, {"type": "datetime"}),
        (int | None, {"type": "integer"}),
        (MaybeCount, {"type": "integer"}),
        (Literal["a", "b"], {"type": "string", "enum": ["a", "b"]}),
        (Literal["only"], {"type": "string", "enum": ["only"]}),
        (Literal["a", "b", None], {"type": "string", "enum": ["a", "b"]}),  # noqa: PYI061 -- the form under test
        (Shade, {"type": "string", "enum": ["light", "dark"]}),
        (Grade, {"type": "string", "enum": ["1", "2"]}),
        (int | str, {"type": "string"}),
        (Decimal, {"type": "string"}),
        (list[int], {"type": "array", "item_type": "integer"}),
        (set[str], {"type": "array", "item_type": "string"}),
        (tuple[int, ...], {"type": "array", "item_type": "integer"}),
        (list[int] | None, {"type": "array", "item_type": "integer"}),
        (MaybeTrays, {"type": "array", "item_type": "integer"}),
        (list[int | str], {"type": "array", "item_type": "string"}),
        (
            list[Literal["a", "b"]],
            {"type": "array", "item_type": "string", "enum": ["a", "b"]},
        ),
    ],
    ids=[
        "str",
        "uuid",
        "int",
        "float",
        "bool",
        "date",
        "datetime",
        "optional",
        "aliased-optional",
        "str-literal",
        "single-literal",
        "nullable-literal",
        "str-enum",
        "int-enum",
        "scalar-union",
        "decimal",
        "list",
        "set",
        "variadic-tuple",
        "optional-list",
        "aliased-optional-list",
        "list-of-union",
        "list-of-choices",
    ],
)
def test_carries_the_supported_shape(annotation, advertised):
    assert descriptor_of(annotation) == advertised


@pytest.mark.parametrize(
    "annotation",
    [
        dict[str, str],
        Address,
        Any,
        list,
        tuple[int, int],
        list[list[int]],
        list[dict[str, str]],
        list[int] | list[str],
        Literal[1, 2],
        Literal[1],
        Priority,
        Color,
        list[Color],
        Literal[None],  # noqa: PYI061 -- the form under test
    ],
    ids=[
        "dict",
        "nested-model",
        "any",
        "bare-list",
        "fixed-tuple",
        "nested-list",
        "list-of-dicts",
        "union-with-a-list",
        "int-literal",
        "single-int-literal",
        "aliased-int-literal",
        "plain-int-enum",
        "list-of-plain-int-enum",
        "no-choices",
    ],
)
def test_rejects_any_other_shape(annotation):
    with pytest.raises(ValueError, match="field 'field'"):
        descriptor_of(annotation)
