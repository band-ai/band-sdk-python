"""Behavioural coverage for outbound ACP session configuration catalogs."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
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
    SessionConfigOption,
    apply_session_config_selections,
)
from tests.integrations.acp.acp_toolkit import FakeACPAgent, Reply, acp_adapter


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


def malformed_catalog_response() -> SimpleNamespace:
    """A transport seam response whose catalog is not ACP schema data."""
    return SimpleNamespace(config_options=["not-an-acp-option"])


def assert_config_error(reply: Reply, expected: dict[str, str]) -> None:
    """Assert the observable failure contract for one rejected configuration."""
    assert reply.outline == ["error"]
    assert reply.events[0]["metadata"]["acp_session_config"] == expected


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
            return_value=SetSessionConfigOptionResponse(
                config_options=[
                    grouped_model.model_copy(update={"current_value": "large"})
                ]
            )
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

    @pytest.mark.asyncio
    async def test_rejects_a_malformed_refreshed_catalog(self) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        set_option = AsyncMock(return_value=malformed_catalog_response())

        with pytest.raises(ACPConfigError, match="malformed catalog"):
            await apply_session_config_selections(
                session_id="session-1",
                config_options=[effort],
                selections={"reasoning_effort": "high"},
                set_option=set_option,
            )

    @pytest.mark.asyncio
    async def test_rejects_a_malformed_catalog_before_a_later_selection(self) -> None:
        model = select_option("model", "small", ["small", "large"])
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        set_option = AsyncMock(return_value=malformed_catalog_response())

        with pytest.raises(ACPConfigError, match="malformed catalog"):
            await apply_session_config_selections(
                session_id="session-1",
                config_options=[model, effort],
                selections={"model": "large", "reasoning_effort": "high"},
                set_option=set_option,
            )

        set_option.assert_awaited_once_with("session-1", "model", "large")

    @pytest.mark.asyncio
    async def test_requires_the_refreshed_catalog_to_acknowledge_the_selection(
        self,
    ) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        set_option = AsyncMock(
            return_value=SetSessionConfigOptionResponse(config_options=[effort])
        )

        with pytest.raises(ACPConfigError, match='did not apply value "high"'):
            await apply_session_config_selections(
                session_id="session-1",
                config_options=[effort],
                selections={"reasoning_effort": "high"},
                set_option=set_option,
            )


class TestACPConfigurationHarness:
    @pytest.mark.asyncio
    async def test_generic_harness_applies_the_remote_effort_catalog(self) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort])

        @agent.on_prompt
        async def configured_prompt(fake: FakeACPAgent, session_id: str) -> None:
            assert fake.config_option_requests == [
                (session_id, "reasoning_effort", "high")
            ]
            await fake.say(session_id, "Configured")

        async def resolve_config(request: ACPConfigRequest) -> dict[str, str]:
            assert request.config_options == (effort,)
            return {"reasoning_effort": "high"}

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            reply = await session.send("Configure the session")

        assert reply.texts == ["Configured"]
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

        assert reply.texts == []
        assert_config_error(
            reply,
            {
                "session_id": "fake-session-1",
                "option_id": "reasoning_effort",
                "selected_value": "unsupported",
            },
        )
        assert agent.prompt_texts() == []
        assert agent.closed_sessions == ["fake-session-1"]

    @pytest.mark.asyncio
    async def test_invalid_falsy_resolver_result_is_reported_without_prompting(
        self,
    ) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort])

        async with acp_adapter(
            agent,
            resolve_session_config=AsyncMock(return_value=[]),
        ) as session:
            reply = await session.send("Configure the session")

        assert reply.texts == []
        assert_config_error(
            reply,
            {
                "session_id": "fake-session-1",
                "option_id": "resolver",
                "selected_value": "",
            },
        )
        assert agent.prompt_texts() == []
        assert agent.closed_sessions == ["fake-session-1"]

    @pytest.mark.asyncio
    async def test_dynamic_catalog_removal_is_reported_without_prompting(
        self,
    ) -> None:
        model = select_option("model", "small", ["small", "large"])
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[model, effort])

        @agent.on_config_option
        async def remove_effort_after_model(
            fake: FakeACPAgent,
            session_id: str,
            option_id: str,
            value: str,
        ) -> list[SessionConfigOption]:
            del fake, session_id
            match option_id, value:
                case "model", "large":
                    return [model.model_copy(update={"current_value": "large"})]
                case unexpected:
                    raise AssertionError(f"Unexpected configuration: {unexpected}")

        async def resolve_config(request: ACPConfigRequest) -> dict[str, str]:
            assert request.config_options == (model, effort)
            return {"model": "large", "reasoning_effort": "high"}

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            reply = await session.send("Configure the session")

        assert reply.texts == []
        assert_config_error(
            reply,
            {
                "session_id": "fake-session-1",
                "option_id": "reasoning_effort",
                "selected_value": "high",
            },
        )
        assert agent.config_option_requests == [("fake-session-1", "model", "large")]
        assert agent.prompt_texts() == []
        assert agent.closed_sessions == ["fake-session-1"]

    @pytest.mark.asyncio
    async def test_independent_rooms_configure_without_waiting_for_each_other(
        self,
    ) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort]).will_say("Configured")
        first_resolver_started = asyncio.Event()
        second_resolver_started = asyncio.Event()
        release_first_resolver = asyncio.Event()

        async def resolve_config(request: ACPConfigRequest) -> None:
            match request.room_id:
                case "room-1":
                    first_resolver_started.set()
                    await release_first_resolver.wait()
                case "room-2":
                    second_resolver_started.set()
                case room_id:
                    raise AssertionError(f"Unexpected room: {room_id}")
            return None

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            first_turn = asyncio.create_task(session.send("First", room="room-1"))
            await first_resolver_started.wait()
            second_turn = asyncio.create_task(session.send("Second", room="room-2"))
            await asyncio.wait_for(second_resolver_started.wait(), timeout=1)

            assert len(agent.sessions) == 2

            release_first_resolver.set()
            first_reply, second_reply = await asyncio.gather(first_turn, second_turn)

        assert first_reply.texts == ["Configured"]
        assert second_reply.texts == ["Configured"]

    @pytest.mark.asyncio
    async def test_same_room_shares_one_inflight_configuration(self) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort]).will_say("Configured")
        resolver_started = asyncio.Event()
        release_resolver = asyncio.Event()

        async def resolve_config(request: ACPConfigRequest) -> None:
            assert request.room_id == "room-1"
            resolver_started.set()
            await release_resolver.wait()
            return None

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            first_turn = asyncio.create_task(session.send("First"))
            await resolver_started.wait()
            second_turn = asyncio.create_task(session.send("Second"))
            await asyncio.sleep(0)

            assert len(agent.sessions) == 1

            release_resolver.set()
            await asyncio.gather(first_turn, second_turn)

        assert len(agent.sessions) == 1
        assert len(agent.prompt_texts()) == 2

    @pytest.mark.asyncio
    async def test_interrupted_configuration_does_not_block_the_next_turn(
        self,
    ) -> None:
        effort = select_option("reasoning_effort", "medium", ["medium", "high"])
        agent = FakeACPAgent(config_options=[effort]).will_say("Configured")
        first_resolver_started = asyncio.Event()
        resolver_calls = 0

        async def resolve_config(request: ACPConfigRequest) -> None:
            nonlocal resolver_calls
            resolver_calls += 1
            assert request.room_id == "room-1"
            if resolver_calls == 1:
                first_resolver_started.set()
                await asyncio.Event().wait()
            return None

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            interrupted_turn = asyncio.create_task(session.send("Interrupted"))
            await first_resolver_started.wait()
            interrupted_turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await interrupted_turn

            reply = await session.send("Retry")

        assert reply.texts == ["Configured"]
        assert [item["session_id"] for item in agent.sessions] == [
            "fake-session-1",
            "fake-session-2",
        ]
        assert agent.closed_sessions == ["fake-session-1"]

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

        assert reply.texts == ["Restored"]
        assert agent.config_option_requests == [
            ("persisted-session", "reasoning_effort", "high")
        ]
