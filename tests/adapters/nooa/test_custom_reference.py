"""Custom payload documentation preserves discoverable, stable input definitions."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, create_model

from band.adapters.nooa.tools import (
    custom_tool_spec,
    room_tools_class,
    room_tools_reference,
)


class TestCustomReference:
    def test_wide_nested_model_keeps_every_required_field(self) -> None:
        fields = {
            f"field_{i}": (str, Field(description=f"Required external field {i}"))
            for i in range(51)
        }
        details = create_model("WideDetails", **fields)
        input_model = create_model(
            "LookupInput", __doc__="Look up an order.", details=(details, ...)
        )
        room = room_tools_class([custom_tool_spec((input_model, BaseModel.model_dump))])

        reference = room_tools_reference(room)

        for name, (_, field) in fields.items():
            assert f"{name}: str" in reference
            assert field.description in reference

    def test_wide_enum_keeps_every_valid_choice(self) -> None:
        choices = Enum(
            "ExternalCode", {f"CODE_{i}": f"external-{i}" for i in range(51)}
        )
        input_model = create_model(
            "LookupInput", __doc__="Look up an order.", code=(choices, ...)
        )
        room = room_tools_class([custom_tool_spec((input_model, BaseModel.model_dump))])

        reference = room_tools_reference(room)

        for choice in choices:
            assert f"{choice.name} = {choice.value!r}" in reference

    def test_nested_container_and_union_fields_reach_the_advertised_reference(
        self,
    ) -> None:
        leaf = create_model(
            "ExternalOrder",
            __doc__="An external order.",
            opaque_code=(int, Field(description="The opaque external order code")),
        )
        details = create_model(
            "Details",
            __doc__="Order details.",
            orders=(list[leaf | None], ...),
        )
        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            details=(details, ...),
        )
        room = room_tools_class([custom_tool_spec((input_model, BaseModel.model_dump))])

        reference = room_tools_reference(room)

        assert "opaque_code: int" in reference
        assert "The opaque external order code" in reference
        assert "orders: list[ExternalOrder | None]" in reference

    def test_recursive_input_is_documented_once(self) -> None:
        input_model = create_model(
            "TreeInput",
            __doc__="A tree node.",
            payload=(str, Field(description="The node payload")),
            child=("TreeInput | None", None),
        )
        input_model.model_rebuild(_types_namespace={"TreeInput": input_model})
        room = room_tools_class([custom_tool_spec((input_model, BaseModel.model_dump))])

        reference = room_tools_reference(room)

        assert "The node payload" in reference
        assert reference.count("class TreeInput(") == 1

    def test_equivalent_alias_generators_produce_the_same_reference(self) -> None:
        references: list[str] = []
        for _ in range(2):
            input_model = create_model(
                "LookupInput",
                __doc__="Look up an order.",
                __config__=ConfigDict(
                    alias_generator=lambda name: (
                        "orderId" if name == "order_id" else name
                    ),
                    strict=True,
                    extra="forbid",
                ),
                order_id=(str, Field(description="The external order id")),
            )
            room = room_tools_class(
                [custom_tool_spec((input_model, BaseModel.model_dump))]
            )
            references.append(room_tools_reference(room))

        assert references[0] == references[1]
        assert "order_id: str;" in references[0]
        assert "validation_alias='orderId'" in references[0]
        assert "'strict': True" in references[0]
        assert "'extra': 'forbid'" in references[0]

    def test_documenting_nested_defaults_never_calls_the_factory(self) -> None:
        calls: list[bool] = []

        def compute() -> list[str]:
            calls.append(True)
            return ["computed"]

        details = create_model(
            "Details",
            __doc__="Order details.",
            tags=(list[str], Field(default_factory=compute)),
        )
        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            details=(details, ...),
        )
        room = room_tools_class([custom_tool_spec((input_model, BaseModel.model_dump))])
        reference = room_tools_reference(room)

        assert "tags: list[str]" in reference
        assert calls == []
