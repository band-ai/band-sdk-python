"""Original validation payloads must reach custom handlers as validated models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    create_model,
    model_validator,
)
from pydantic.fields import FieldInfo

from band.adapters.nooa.tools import custom_tool_spec, room_tools_class
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.nooa import custom_room_call, room_call, scripted_llm
from tests.adapters.nooa.kit import prompt_text, run_turn, started_adapter


class TestValidationAliases:
    @pytest.mark.parametrize(
        ("field", "validation_input"),
        [
            pytest.param(Field(), {"order_id": "A1"}, id="field-name"),
            pytest.param(Field(alias="orderId"), {"orderId": "A1"}, id="alias"),
            pytest.param(
                Field(serialization_alias="orderId"),
                {"order_id": "A1"},
                id="serialization-only",
            ),
            pytest.param(
                Field(validation_alias="orderId"),
                {"orderId": "A1"},
                id="validation-alias",
            ),
            pytest.param(
                Field(validation_alias=AliasChoices("orderId", "legacyId")),
                {"legacyId": "A1"},
                id="alias-choices",
            ),
            pytest.param(
                Field(validation_alias=AliasPath("order", "ids", 0)),
                {"order": {"ids": ["A1"]}},
                id="alias-path",
            ),
            pytest.param(
                Field(
                    validation_alias=AliasChoices(
                        AliasPath("order", "ids", 0), "orderId"
                    )
                ),
                {"orderId": "A1"},
                id="choices-with-path",
            ),
            pytest.param(
                Field(
                    alias="legacyId",
                    validation_alias="orderId",
                    serialization_alias="outputId",
                ),
                {"orderId": "A1"},
                id="distinct-validation-and-serialization",
            ),
        ],
    )
    async def test_original_payload_calls_handler_once_with_validated_input(
        self,
        tools: FakeAgentTools,
        field: FieldInfo,
        validation_input: dict[str, Any],
    ) -> None:
        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            __config__=ConfigDict(strict=True),
            order_id=(str, field),
        )
        expected = input_model.model_validate(validation_input)
        received: list[BaseModel] = []

        def lookup(args: BaseModel) -> str:
            received.append(args)
            return f"ORDER_OK:{args.model_dump()['order_id']}"

        llm = scripted_llm(
            f"print({custom_room_call('lookup', validation_input)})",
            room_call(BandTool.NO_REPLY),
        )
        adapter = await started_adapter(llm, additional_tools=[(input_model, lookup)])

        await run_turn(adapter, tools)

        assert received == [expected]
        assert "ORDER_OK:A1" in prompt_text(llm)

    async def test_invalid_field_value_does_not_reach_handler(
        self, tools: FakeAgentTools
    ) -> None:
        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            __config__=ConfigDict(strict=True),
            order_id=(int, Field(alias="orderId")),
        )
        received: list[BaseModel] = []

        def lookup(args: BaseModel) -> str:
            received.append(args)
            return "unexpected"

        llm = scripted_llm(
            custom_room_call("lookup", {"orderId": "123"}),
            room_call(BandTool.NO_REPLY),
        )
        adapter = await started_adapter(llm, additional_tools=[(input_model, lookup)])

        await run_turn(adapter, tools)

        assert received == []
        assert "Input should be a valid integer" in prompt_text(llm)

    async def test_original_model_validates_the_exact_payload_once(
        self, tools: FakeAgentTools
    ) -> None:
        seen: list[tuple[type[BaseModel], Any]] = []
        received: list[BaseModel] = []

        def observe(cls: type[BaseModel], value: Any) -> Any:
            seen.append((cls, value))
            return value

        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            __config__=ConfigDict(strict=True, extra="forbid"),
            __validators__={"observe": model_validator(mode="before")(observe)},
            x=(int, Field(alias="y")),
            y=(int, Field(alias="z")),
            tags=(list[str], Field(default_factory=lambda data: [str(data["x"])])),
        )

        def lookup(args: BaseModel) -> dict[str, Any]:
            received.append(args)
            return args.model_dump()

        room = room_tools_class([custom_tool_spec((input_model, lookup))])(
            emit_tool_calls=False
        )
        payload = {"y": 1, "z": 2}
        async with room.bound_to(tools):
            result = await room.lookup(arguments=payload)

        assert result == {"x": 1, "y": 2, "tags": ["1"]}
        assert len(seen) == 1
        assert seen[0][0] is input_model
        assert seen[0][1] is payload
        assert type(received[0]) is input_model

    async def test_model_configuration_can_disable_validation_aliases(
        self, tools: FakeAgentTools
    ) -> None:
        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            __config__=ConfigDict(validate_by_alias=False, validate_by_name=True),
            order_id=(str, Field(alias="orderId")),
        )
        received: list[str] = []

        def lookup(args: BaseModel) -> str:
            value = args.model_dump()["order_id"]
            received.append(value)
            return value

        llm = scripted_llm(
            custom_room_call("lookup", {"order_id": "A1"}),
            room_call(BandTool.NO_REPLY),
        )
        adapter = await started_adapter(llm, additional_tools=[(input_model, lookup)])
        await run_turn(adapter, tools)
        assert received == ["A1"]
        assert "validate_by_alias': False" in prompt_text(llm)

    @pytest.mark.parametrize(
        ("payload", "error_text"),
        [
            ({"details": {"externalCode": 7}}, None),
            ({"details": {"externalCode": "7"}}, "Input should be a valid integer"),
            (
                {"details": {"externalCode": 7, "unknown": True}},
                "Extra inputs are not permitted",
            ),
            (
                {"details": {"externalCode": 7}, "unknown": True},
                "Extra inputs are not permitted",
            ),
        ],
        ids=["nested-alias", "nested-strictness", "nested-extra", "outer-extra"],
    )
    async def test_nested_models_keep_their_original_validation_rules(
        self, tools: FakeAgentTools, payload: dict[str, Any], error_text: str | None
    ) -> None:
        details_model = create_model(
            "Details",
            __config__=ConfigDict(strict=True, extra="forbid"),
            code=(int, Field(validation_alias="externalCode")),
        )
        input_model = create_model(
            "LookupInput",
            __doc__="Look up an order.",
            __config__=ConfigDict(extra="forbid"),
            details=(details_model, ...),
        )
        received: list[dict[str, Any]] = []

        def lookup(args: BaseModel) -> str:
            received.append(args.model_dump())
            return "LOOKUP_OK"

        llm = scripted_llm(
            custom_room_call("lookup", payload), room_call(BandTool.NO_REPLY)
        )
        adapter = await started_adapter(llm, additional_tools=[(input_model, lookup)])
        await run_turn(adapter, tools)
        assert received == ([{"details": {"code": 7}}] if error_text is None else [])
        if error_text is not None:
            assert error_text in prompt_text(llm)
