"""The field shapes a Parlant custom tool can carry: one row per shape.

``customschema``'s module docstring is the table these rows pin.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from pydantic import BaseModel, Field, create_model
from typing_extensions import TypeAliasType

from band.integrations.parlant.customschema import Descriptor, describe_custom_tool
from tests.integrations.parlant.samples import MaybeTrays, Shade


class Grade(enum.IntEnum):
    ECONOMY = 1
    PREMIUM = 2


class Color(enum.Enum):
    RED = 1
    BLUE = 2


class Mixed(enum.Enum):
    A = "a"
    B = 1


class Finish(enum.Enum):
    """The paint finish."""

    MATTE = "matte"


class Address(BaseModel):
    street: str


MaybeCount = TypeAliasType("MaybeCount", int | None)
Priority = TypeAliasType("Priority", Literal["low", "high"])
IntPriority = TypeAliasType("IntPriority", Literal[1, 2])


def descriptor_of(annotation: Any) -> Descriptor:
    """The descriptor Parlant advertises for a lone optional field of *annotation*."""
    model = create_model("ShapeInput", field=(annotation, None))
    [field] = describe_custom_tool(model).fields
    return field.descriptor


ACCEPTED = [
    ("str", str, {"type": "string"}),
    ("uuid", UUID, {"type": "string"}),
    ("int", int, {"type": "integer"}),
    ("float", float, {"type": "number"}),
    ("bool", bool, {"type": "boolean"}),
    ("date", date, {"type": "date"}),
    ("datetime", datetime, {"type": "datetime"}),
    ("optional", int | None, {"type": "integer"}),
    ("aliased-optional", MaybeCount, {"type": "integer"}),
    ("str-literal", Literal["a", "b"], {"type": "string", "enum": ["a", "b"]}),
    ("single-literal", Literal["only"], {"type": "string", "enum": ["only"]}),
    (
        "nullable-literal",
        Literal["a", "b", None],  # noqa: PYI061 -- the form under test
        {"type": "string", "enum": ["a", "b"]},
    ),
    ("aliased-literal", Priority, {"type": "string", "enum": ["low", "high"]}),
    ("str-enum", Shade, {"type": "string", "enum": ["light", "dark"]}),
    ("union-with-a-literal", Literal["auto"] | int, {"type": "string"}),
    ("optional-union", Literal["auto"] | int | None, {"type": "string"}),
    ("union-with-a-choice", Shade | int, {"type": "string"}),
    (
        "union-of-choices",
        Shade | Literal["clear"],
        {"type": "string", "enum": ["light", "dark", "clear"]},
    ),
    (
        "overlapping-union-of-choices",
        Shade | Literal["light"],
        {"type": "string", "enum": ["light", "dark"]},
    ),
    ("decimal", Decimal, {"type": "string"}),
    ("list", list[int], {"type": "array", "item_type": "integer"}),
    ("set", set[str], {"type": "array", "item_type": "string"}),
    ("variadic-tuple", tuple[int, ...], {"type": "array", "item_type": "integer"}),
    ("optional-list", list[int] | None, {"type": "array", "item_type": "integer"}),
    ("aliased-optional-list", MaybeTrays, {"type": "array", "item_type": "integer"}),
    (
        "list-of-union",
        list[Literal["auto"] | int],
        {"type": "array", "item_type": "string"},
    ),
    (
        "list-of-choices",
        list[Literal["a", "b"]],
        {"type": "array", "item_type": "string", "enum": ["a", "b"]},
    ),
    (
        "list-of-enum",
        list[Shade],
        {"type": "array", "item_type": "string", "enum": ["light", "dark"]},
    ),
    (
        "described-enum",
        Finish,
        {"type": "string", "enum": ["matte"], "description": "The paint finish."},
    ),
    (
        "optional-described-type",
        Annotated[int, Field(description="Spend cap")] | None,
        {"type": "integer", "description": "Spend cap"},
    ),
    (
        "field-description-over-the-types",
        Annotated[
            Annotated[str, Field(description="Trim style")] | None,
            Field(description="Trim to paint"),
        ],
        {"type": "string", "description": "Trim to paint"},
    ),
    (
        "list-item-description",
        list[Annotated[str, Field(description="A wall")]],
        {"type": "array", "item_type": "string", "description": "A wall"},
    ),
]

REJECTED = [
    ("dict", dict[str, str]),
    ("nested-model", Address),
    ("any", Any),
    ("bare-list", list),
    ("fixed-tuple", tuple[int, int]),
    ("nested-list", list[list[int]]),
    ("list-of-dicts", list[dict[str, str]]),
    ("union-with-a-list", list[int] | list[str]),
    ("union-with-str", int | str),
    ("bool-or-str", bool | str),
    ("optional-union-with-str", int | str | None),
    ("list-of-union-with-str", list[int | str]),
    ("int-literal", Literal[1, 2]),
    ("mixed-literal", Literal["a", 1]),
    ("mixed-enum", Mixed),
    ("bool-literal", Literal[True]),
    ("aliased-int-literal", IntPriority),
    ("int-enum", Grade),
    ("plain-int-enum", Color),
    ("list-of-int-enum", list[Grade]),
    ("union-of-int-choices", Literal[1, 2] | Color),
    ("union-with-int-choices", Shade | Grade),
    ("no-choices", Literal[None]),  # noqa: PYI061 -- the form under test
]


@pytest.mark.parametrize(
    ("annotation", "advertised"),
    [row[1:] for row in ACCEPTED],
    ids=[row[0] for row in ACCEPTED],
)
def test_carries_the_supported_shape(annotation, advertised):
    assert descriptor_of(annotation) == advertised


@pytest.mark.parametrize(
    "annotation", [row[1] for row in REJECTED], ids=[row[0] for row in REJECTED]
)
def test_rejects_any_other_shape(annotation):
    """Parlant sends every argument as text, so only a string choice is sure
    to validate; any other shape fails when the adapter is built."""
    with pytest.raises(ValueError, match="field 'field'"):
        descriptor_of(annotation)
