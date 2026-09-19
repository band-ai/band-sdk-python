"""Behavioral coverage for the generic ACP dynamic-configuration example."""

from __future__ import annotations

import pytest
from acp.schema import (
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
)

from band.adapters import ACPConfigRequest
from tests.loaders import load_script_module

generic_acp = load_script_module(
    "examples/acp/clients/generic.py", "generic_acp_configuration_example"
)


def select_option(
    option_id: str,
    current_value: str,
    values: list[str],
) -> SessionConfigOptionSelect:
    """Create one direct ACP select option."""
    return SessionConfigOptionSelect(
        id=option_id,
        name=option_id,
        type="select",
        current_value=current_value,
        options=[
            SessionConfigSelectOption(value=value, name=value) for value in values
        ],
    )


@pytest.mark.asyncio
async def test_example_selects_one_requested_advertised_value() -> None:
    model = SessionConfigOptionSelect(
        id="model",
        name="Model",
        type="select",
        current_value="small",
        options=[
            SessionConfigSelectGroup(
                group="recommended",
                name="Recommended",
                options=[
                    SessionConfigSelectOption(value="small", name="Small"),
                    SessionConfigSelectOption(value="large", name="Large"),
                ],
            )
        ],
    )
    request = ACPConfigRequest(
        room_id="room-1",
        session_id="session-1",
        config_options=(
            model,
            select_option("reasoning_effort", "medium", ["low", "medium", "high"]),
        ),
    )

    selections = await generic_acp.choose_session_config(
        request,
        preferences={"model": "large", "reasoning_effort": "high"},
    )

    assert selections == {"model": "large"}


@pytest.mark.asyncio
async def test_example_ignores_an_unadvertised_preference() -> None:
    request = ACPConfigRequest(
        room_id="room-1",
        session_id="session-1",
        config_options=(
            select_option("reasoning_effort", "medium", ["low", "medium"]),
        ),
    )

    selections = await generic_acp.choose_session_config(
        request,
        preferences={"reasoning_effort": "high"},
    )

    assert selections == {}
