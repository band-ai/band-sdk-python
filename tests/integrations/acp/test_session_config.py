"""Behavioural coverage for outbound ACP session configuration catalogs."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from acp.schema import (
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
    SetSessionConfigOptionResponse,
)

from band.core.exceptions import BandConfigError
from band.core.model_catalog import ModelCatalog, ModelChoice, ModelSelection
from band.integrations.acp import session_config
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.model_selection import ACPModelOptions
from band.integrations.acp.session_config import (
    CONFIG_FAILURE_PREFIX,
    RESOLVER_CONFIG_OPTION_ID,
    ACPConfigError,
    ACPConfigRequest,
    SessionConfigOption,
    apply_session_config_selections,
)
from tests.integrations.acp.acp_toolkit import (
    FakeACPAgent,
    Reply,
    acp_adapter,
    select_option,
    started_acp_adapter,
)


def malformed_catalog_response() -> SimpleNamespace:
    """A transport seam response whose catalog is not ACP schema data."""
    return SimpleNamespace(config_options=["not-an-acp-option"])


def assert_config_error(reply: Reply, expected: dict[str, str]) -> None:
    """Assert the observable failure contract for one rejected configuration."""
    assert reply.outline == ["error"]
    assert reply.events[0]["metadata"]["failure"]["detail"] == expected


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

        with pytest.raises(ACPConfigError) as rejected:
            await apply_session_config_selections(
                session_id="session-1",
                config_options=[model, effort],
                selections={"model": "auto", "reasoning_effort": "high"},
                set_option=set_option,
            )

        assert str(rejected.value) == (
            'ACP session offers no config option "reasoning_effort"; available: model.'
        )
        set_option.assert_awaited_once_with("session-1", "model", "auto")

    @pytest.mark.asyncio
    async def test_an_option_on_an_empty_catalog_is_refused_naming_none(self) -> None:
        with pytest.raises(ACPConfigError) as rejected:
            await apply_session_config_selections(
                session_id="session-1",
                config_options=[],
                selections={"model": "auto"},
                set_option=AsyncMock(),
            )

        assert str(rejected.value) == (
            'ACP session offers no config option "model"; available: (none).'
        )

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
        assert reply.errors == [
            (
                f"{CONFIG_FAILURE_PREFIX}ACP config value "
                '"unsupported" is not advertised for option "reasoning_effort"; '
                "available: medium, high."
            )
        ]
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
                "option_id": RESOLVER_CONFIG_OPTION_ID,
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

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            interrupted_turn = asyncio.create_task(session.send("Interrupted"))
            await first_resolver_started.wait()
            interrupted_turn.cancel()
            with pytest.raises(asyncio.CancelledError):
                await interrupted_turn

            reply = await session.send("Retry")

        assert reply.texts == ["Configured"]
        assert agent.session_ids() == [
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

    @pytest.mark.asyncio
    async def test_restored_session_closes_when_configuration_fails(self) -> None:
        agent = FakeACPAgent(supports_session_load=True).knows_session(
            "persisted-session"
        )

        async def resolve_config(request: ACPConfigRequest) -> dict[str, str]:
            assert request.config_options == ()
            return {"model": "unsupported"}

        async with acp_adapter(agent, resolve_session_config=resolve_config) as session:
            reply = await session.send(
                "Resume",
                bootstrap=True,
                history=ACPClientSessionState(
                    room_to_session={"room-1": "persisted-session"}
                ),
            )

        assert_config_error(
            reply,
            {
                "session_id": "persisted-session",
                "option_id": "model",
                "selected_value": "unsupported",
            },
        )
        assert agent.closed_sessions == ["persisted-session"]


class ThinkingIdAdapter(ACPClientAdapter):
    """An ACP adapter for an agent that publishes uncategorized ``model`` and
    ``thinking`` selects, the way OMP does."""

    def locate_model_options(
        self, options: Sequence[SessionConfigOption]
    ) -> ACPModelOptions:
        selects = {
            option.id: option
            for option in options
            if isinstance(option, SessionConfigOptionSelect)
        }
        return ACPModelOptions(
            model=selects.get("model"), effort=selects.get("thinking")
        )


class TestTypedModelSelection:
    def test_the_catalog_knows_efforts_only_for_the_current_model(self) -> None:
        # ACP advertises efforts for the active model alone: the others are
        # unknown (None), which a host must not show as "offers none" (()).
        located = ACPModelOptions(
            model=select_option("model", "large", ["small", "large"]),
            effort=select_option("thinking", "low", ["low", "high"]),
        )

        assert located.model_catalog() == ModelCatalog(
            models=(
                ModelChoice(id="small", label="Small", efforts=None),
                ModelChoice(
                    id="large",
                    label="Large",
                    efforts=("low", "high"),
                    default_effort="low",
                ),
            ),
            current_model="large",
        )

    @pytest.mark.asyncio
    async def test_an_adapter_can_locate_selects_that_carry_no_category(
        self,
    ) -> None:
        agent = FakeACPAgent(
            config_options=[
                select_option("model", "small", ["small", "large"]),
                select_option("thinking", "low", ["low", "high"]),
            ]
        ).will_say("Configured")
        adapter = ThinkingIdAdapter(
            command="fake-agent",
            inject_band_tools=False,
            model_selection=ModelSelection(model="large", reasoning_effort="high"),
        )

        async with started_acp_adapter(adapter, agent) as session:
            reply = await session.send("Hello")

        assert reply.texts == ["Configured"]
        assert agent.config_selections() == [("model", "large"), ("thinking", "high")]

    @pytest.mark.asyncio
    async def test_a_switch_back_reaches_an_agent_that_only_replies_with_catalogs(
        self,
    ) -> None:
        # No config_option_update is pushed, so the set replies are the only
        # record that the session moved off "small".
        agent = FakeACPAgent(
            config_options=[
                select_option("model", "small", ["small", "large"], category="model")
            ]
        ).will_say("ok")

        async with acp_adapter(agent) as session:
            await session.send("Hello")
            for model in ("large", "small"):
                await session.adapter.apply_model_selection(
                    ModelSelection(model=model), room_id="room-1"
                )

        assert agent.config_selections() == [("model", "large"), ("model", "small")]

    @pytest.mark.asyncio
    async def test_a_switch_back_after_a_timed_out_switch_reaches_the_agent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The agent applies "large" but its reply misses the deadline, so the
        # client never sees the session leave "small".
        monkeypatch.setattr(session_config, "SESSION_CONFIG_TIMEOUT_SECONDS", 0.05)
        agent = FakeACPAgent(
            config_options=[
                select_option("model", "small", ["small", "large"], category="model")
            ]
        ).will_say("ok")

        async with acp_adapter(agent) as session:
            await session.send("Hello")
            gate = agent.holds_config_replies()
            with pytest.raises(BandConfigError, match="did not respond"):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="large"), room_id="room-1"
                )
            gate.release.set()
            await session.adapter.apply_model_selection(
                ModelSelection(model="small"), room_id="room-1"
            )

        assert agent.current_value("model") == "small"

    @pytest.mark.asyncio
    async def test_a_model_is_refused_when_the_agent_advertises_no_model_option(
        self,
    ) -> None:
        agent = FakeACPAgent().will_say("unreachable")

        async with acp_adapter(
            agent, model_selection=ModelSelection(model="large")
        ) as session:
            reply = await session.send("Hello")

        assert reply.errors == [
            f"{CONFIG_FAILURE_PREFIX}ACP session advertises no model option."
        ]
        assert agent.prompt_texts() == []
