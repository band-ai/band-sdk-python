"""The field shapes a Parlant custom tool can carry: one row per shape.

``customschema``'s module docstring is the table these rows pin.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest
from pydantic import (
    AfterValidator,
    AliasChoices,
    AliasPath,
    BaseModel,
    Field,
    conlist,
    create_model,
)
from pydantic.fields import FieldInfo
from typing_extensions import TypeAliasType

from band.integrations.parlant.customschema import Descriptor, describe_custom_tool
from tests.integrations.parlant.samples import Color, Grade, MaybeTrays, Shade


class Address(BaseModel):
    street: str


def refuse(value: object) -> object:
    """A validator no probe may run: it fails every value, and not as a ValueError."""
    raise KeyError(value)


MaybeCount = TypeAliasType("MaybeCount", int | None)
Priority = TypeAliasType("Priority", Literal[1, 2])
# The length limit rides on the type itself, so it reaches the field's annotation.
PickOne = TypeAliasType(
    "PickOne", Annotated[list[Literal["a", "b"]], Field(max_length=1)]
)


def descriptor_of(annotation: Any, field: FieldInfo | None = None) -> Descriptor:
    """The descriptor Parlant advertises for a lone optional field of *annotation*."""
    model = create_model("ShapeInput", field=(annotation, field or Field(None)))
    [described] = describe_custom_tool(model).fields
    return described.descriptor


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
        (int | str | None, {"type": "string"}),
        (Shade | int, {"type": "string"}),
        (Shade | Grade, {"type": "string", "enum": ["light", "dark", "1", "2"]}),
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
        (
            list[Shade],
            {"type": "array", "item_type": "string", "enum": ["light", "dark"]},
        ),
        (
            list[Annotated[str, Field(description="A wall")]],
            {"type": "array", "item_type": "string", "description": "A wall"},
        ),
        (PickOne, {"type": "array", "item_type": "string", "enum": ["a", "b"]}),
        (
            conlist(Literal["a", "b"], min_length=2) | None,
            {"type": "array", "item_type": "string", "enum": ["a", "b"]},
        ),
        (
            list[Annotated[Literal["a", "b"], AfterValidator(refuse)]],
            {"type": "array", "item_type": "string", "enum": ["a", "b"]},
        ),
        (
            Annotated[Literal["a", "b"], AfterValidator(refuse)] | None,
            {"type": "string", "enum": ["a", "b"]},
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
        "optional-scalar-union",
        "union-with-a-choice",
        "union-of-choices",
        "decimal",
        "list",
        "set",
        "variadic-tuple",
        "optional-list",
        "aliased-optional-list",
        "list-of-union",
        "list-of-choices",
        "list-of-enum",
        "list-item-description",
        "aliased-list-length-limit",
        "optional-list-length-limit",
        "list-item-validator",
        "optional-choice-validator",
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
        Literal[1, 2] | Color,
        list[Literal[1, 2] | Color],
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
        "union-of-int-choices",
        "list-of-union-of-int-choices",
        "no-choices",
    ],
)
def test_rejects_any_other_shape(annotation):
    with pytest.raises(ValueError, match="field 'field'"):
        descriptor_of(annotation)


@pytest.mark.parametrize(
    ("field", "key"),
    [
        (Field(None, alias="pick"), "pick"),
        (Field(None, validation_alias="pick"), "pick"),
        (Field(None, validation_alias=AliasChoices("pick", "p")), "pick"),
        (Field(None, validation_alias=AliasChoices(AliasPath("p", 0), "pick")), "pick"),
        (Field(None, validation_alias=AliasPath("pick")), "field"),
    ],
    ids=[
        "alias",
        "validation-alias",
        "alias-choices",
        "alias-choices-after-a-deep-path",
        "path",
    ],
)
def test_checks_an_aliased_fields_choices(field, key):
    """The choices are checked under whatever key pydantic advertises the field."""
    with pytest.raises(ValueError, match=f"field '{key}'.*choices as strings"):
        descriptor_of(Literal[1, 2], field)
