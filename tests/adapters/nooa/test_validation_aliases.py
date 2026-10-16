"""Advertised Python arguments must reach custom handlers as validated models."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field, create_model
from pydantic.fields import FieldInfo

from band.runtime.tools import BandTool
from band.testing import FakeAgentTools
from band.testing.nooa import room_call, scripted_llm
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
    async def test_advertised_field_name_calls_handler_once_with_validated_input(
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
            f"print({room_call('lookup', {'order_id': 'A1'})})",
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
            room_call("lookup", {"order_id": "123"}),
            room_call(BandTool.NO_REPLY),
        )
        adapter = await started_adapter(llm, additional_tools=[(input_model, lookup)])

        await run_turn(adapter, tools)

        assert received == []
        assert "Input should be a valid integer" in prompt_text(llm)
