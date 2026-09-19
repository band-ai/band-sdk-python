"""Behavioural coverage for outbound ACP session configuration catalogs."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from acp.schema import (
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)

from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.session_config import (
    ACPConfigRequest,
    ACPConfigError,
    apply_session_config_selections,
)
from tests.integrations.acp.acp_toolkit import FakeACPAgent, acp_adapter


def select_option(
    option_id: str,
    current_value: str,
    values: list[str],
) -> SessionConfigOptionSelect:
    """A concise ACP select catalog entry for one test."""
    return SessionConfigOptionSelect(
        id=option_id,
        name=option_id.replace("_", " ").title(),
        type="select",
        current_value=current_value,
        options=[
            SessionConfigSelectOption(value=value, name=value.title())
            for value in values
        ],
    )


class TestApplySessionConfigSelections:
    @pytest.mark.asyncio
    async def test_revalidates_each_selection_against_the_refreshed_catalog(
        self,
    ) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        model = select_option("model", "sonnet", ["sonnet", "auto"])
        set_option = AsyncMock(
            side_effect=[
                SetSessionConfigOptionResponse(
                    config_options=[
                        select_option("reasoning_effort", "high", ["medium", "high"]),
                        model,
                    ]
                ),
                SetSessionConfigOptionResponse(
                    config_options=[select_option("model", "auto", ["sonnet", "auto"])]
                ),
            ]
        )

        await apply_session_config_selections(
            session_id="session-1",
            config_options=[effort, model],
            selections={"reasoning_effort": "high", "model": "auto"},
            set_option=set_option,
        )

        assert set_option.await_args_list[0].args == (
            "session-1",
            "reasoning_effort",
            "high",
        )
        assert set_option.await_args_list[1].args == ("session-1", "model", "auto")

    @pytest.mark.asyncio
    async def test_accepts_a_value_from_a_grouped_model_list(self) -> None:
        grouped_model = SessionConfigOptionSelect(
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
        set_option = AsyncMock(
            return_value=SetSessionConfigOptionResponse(config_options=[grouped_model])
        )

        await apply_session_config_selections(
            session_id="session-1",
            config_options=[grouped_model],
            selections={"model": "large"},
            set_option=set_option,
        )

        set_option.assert_awaited_once_with("session-1", "model", "large")

    @pytest.mark.asyncio
    async def test_fails_when_a_prior_selection_removes_a_later_effort_option(
        self,
    ) -> None:
        model = select_option("model", "sonnet", ["sonnet", "auto"])
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        set_option = AsyncMock(
            return_value=SetSessionConfigOptionResponse(
                config_options=[select_option("model", "auto", ["sonnet", "auto"])]
            )
        )

        with pytest.raises(ACPConfigError, match='"reasoning_effort" is not available'):
            await apply_session_config_selections(
                session_id="session-1",
                config_options=[model, effort],
                selections={"model": "auto", "reasoning_effort": "high"},
                set_option=set_option,
            )

        set_option.assert_awaited_once_with("session-1", "model", "auto")


class TestACPConfigurationHarness:
    @pytest.mark.asyncio
    async def test_generic_harness_applies_the_remote_effort_catalog(self) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort]).will_say("Configured")

        async def resolve_config(request: ACPConfigRequest) -> dict[str, str]:
            assert request.config_options == (effort,)
            return {"reasoning_effort": "high"}

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            reply = await session.send("Configure the session")

        assert reply.messages[0]["content"] == "Configured"
        assert agent.config_option_requests == [
            ("fake-session-1", "reasoning_effort", "high")
        ]

    @pytest.mark.asyncio
    async def test_invalid_selection_is_reported_without_prompting(self) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort])

        async def resolve_config(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {"reasoning_effort": "unsupported"}

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            reply = await session.send("Configure the session")

        assert reply.messages == []
        assert reply.events[0]["message_type"] == "error"
        assert reply.events[0]["metadata"]["acp_session_config"] == {
            "session_id": "fake-session-1",
            "option_id": "reasoning_effort",
            "selected_value": "unsupported",
        }
        assert agent.prompts == []

    @pytest.mark.asyncio
    async def test_restored_session_uses_the_same_configuration_path(self) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = (
            FakeACPAgent(config_options=[effort], supports_session_load=True)
            .knows_session("persisted-session")
            .will_say("Restored")
        )

        async def resolve_config(request: ACPConfigRequest) -> dict[str, str]:
            assert request.session_id == "persisted-session"
            return {"reasoning_effort": "high"}

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            reply = await session.send(
                "Resume",
                bootstrap=True,
                history=ACPClientSessionState(
                    room_to_session={"room-1": "persisted-session"}
                ),
            )

        assert reply.messages[0]["content"] == "Restored"
        assert agent.config_option_requests == [
            ("persisted-session", "reasoning_effort", "high")
        ]
