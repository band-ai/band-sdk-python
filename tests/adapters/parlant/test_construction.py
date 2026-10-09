"""ParlantAdapter construction and config validation.

Shared adapter behavior (defaults, custom kwargs, history_converter) lives in
tests/framework_conformance/test_adapter_conformance.py.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import (
    BaseModel,
    Field,
    ValidationInfo,
    create_model,
    field_validator,
    model_validator,
)

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from tests.adapters.parlant.helpers import LOOKUP


def test_borrowed_server_and_agent_are_exposed(mock_parlant_server, mock_parlant_agent):
    adapter = ParlantAdapter(
        server=mock_parlant_server, parlant_agent=mock_parlant_agent
    )

    assert adapter.server is mock_parlant_server
    assert adapter.parlant_agent is mock_parlant_agent


def test_owned_server_and_agent_are_unavailable_before_start():
    adapter = ParlantAdapter()

    with pytest.raises(RuntimeError, match="not running yet"):
        _ = adapter.server
    with pytest.raises(RuntimeError, match="not created yet"):
        _ = adapter.parlant_agent


@pytest.mark.parametrize(
    "config",
    [
        ParlantAdapterConfig(system_prompt="You are a custom assistant."),
        ParlantAdapterConfig(custom_section="Be helpful."),
    ],
)
def test_prompt_params_rejected_with_borrowed_agent(
    mock_parlant_server, mock_parlant_agent, config
):
    """system_prompt/custom_section only shape an adapter-created agent."""
    with pytest.raises(ValueError, match="parlant_agent"):
        ParlantAdapter(
            config, server=mock_parlant_server, parlant_agent=mock_parlant_agent
        )


def test_borrowed_agent_requires_its_server(mock_parlant_agent):
    with pytest.raises(ValueError, match="requires the server"):
        ParlantAdapter(parlant_agent=mock_parlant_agent)


@pytest.mark.parametrize(
    "owned_server_kwargs",
    [{"nlp_service": "svc"}, {"server_options": {"host": "127.0.0.1"}}],
)
def test_owned_server_options_rejected_with_borrowed_server(
    mock_parlant_server, owned_server_kwargs
):
    with pytest.raises(ValueError, match="caller-provided server"):
        ParlantAdapter(server=mock_parlant_server, **owned_server_kwargs)


@pytest.mark.parametrize("field", ["response_timeout", "response_poll"])
@pytest.mark.parametrize("value", [0, -1.0])
def test_config_rejects_non_positive_response_budget(field, value):
    with pytest.raises(ValueError, match="greater than 0"):
        ParlantAdapterConfig(**{field: value})


async def lookup(args: BaseModel) -> str:
    return "found"


# A custom tool's name is its model's class name, so shadowing a Band tool
# needs a snake_case class name.
ShadowingInput = create_model("band_send_messageInput", content=(str, ...))


class ContextInput(BaseModel):
    """Collides with Parlant's tool context."""

    context: str


class KeywordAliasInput(BaseModel):
    """Advertises a Python keyword."""

    sender: str = Field(alias="from")


class DictInput(BaseModel):
    """Has an object field."""

    tags: dict[str, str]


@pytest.mark.parametrize(
    ("additional_tools", "named"),
    [
        ([(ShadowingInput, lookup)], "band_send_message"),
        ([LOOKUP, LOOKUP], "lookup"),
        ([(ContextInput, lookup)], "context"),
        ([(KeywordAliasInput, lookup)], "from"),
        ([(DictInput, lookup)], "tags"),
    ],
    ids=[
        "shadows-band-tool",
        "duplicate-name",
        "context-field",
        "keyword-alias",
        "unsupported-shape",
    ],
)
def test_rejects_custom_tools_parlant_cannot_offer(additional_tools, named):
    with pytest.raises(ValueError, match=named):
        ParlantAdapter(additional_tools=additional_tools)


class PickOneInput(BaseModel):
    """Has a length limit below its number of choices."""

    colors: list[Literal["red", "green", "blue"]] = Field(max_length=1)


class ScheduleInput(BaseModel):
    """Has a choice whose validator reads an earlier field."""

    start: int
    unit: Literal["day", "week"]

    @field_validator("unit")
    @classmethod
    def needs_start(cls, unit: str, info: ValidationInfo) -> str:
        assert info.data["start"] >= 0
        return unit


class ChargeInput(BaseModel):
    """Has a model validator that reads every field."""

    kind: Literal["a", "b"]
    amount: int

    @model_validator(mode="before")
    @classmethod
    def needs_amount(cls, data: dict[str, object]) -> dict[str, object]:
        assert data["amount"] is not None
        return data


@pytest.mark.parametrize(
    "input_model",
    [PickOneInput, ScheduleInput, ChargeInput],
    ids=["list-length-limit", "field-validator", "model-validator"],
)
def test_accepts_choices_whose_checks_need_the_whole_call(input_model):
    """A choice is checked against its field's type alone; length limits and
    validators see a real call's full input, never a probe's partial one."""
    ParlantAdapter(additional_tools=[(input_model, lookup)])
