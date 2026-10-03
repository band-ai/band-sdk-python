"""Tests for CopilotACPAdapter.

CopilotACPAdapter is a thin specialization of ACPClientAdapter — its contract is
how a CopilotACPAdapterConfig maps onto the base adapter's transport, auth, and
system-context wiring. Runtime/on_started behavior is covered by the generic ACP
client suite (tests/integrations/acp/); the model-selection tests drive the
adapter against an in-process fake shaped like Copilot's live catalog.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from band.adapters.copilot_acp import (
    DEFAULT_COPILOT_COMMAND,
    CopilotACPAdapter,
    CopilotACPAdapterConfig,
)
from band.core.exceptions import BandConfigError
from band.core.model_catalog import ModelSelection
from band.integrations.acp import session_config
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import NoopACPClientProfile
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.session_config import (
    CONFIG_FAILURE_PREFIX,
    ACPConfigRequest,
)
from tests.integrations.acp.acp_toolkit.agent import (
    EFFORT_OPTION_ID,
    MODEL_OPTION_ID,
    FakeACPAgent,
)
from tests.integrations.acp.acp_toolkit.harness import (
    DEFAULT_ROOM,
    AcpSession,
    launch_for,
    started_acp_adapter,
)


class TestCopilotACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(CopilotACPAdapter, ACPClientAdapter)

    @pytest.mark.asyncio
    async def test_launches_copilot_with_its_ambient_login_by_default(
        self, tmp_path: Path
    ) -> None:
        # No token and no env -> the CLI's ambient login (stored / gh / BYOK).
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter)

        assert launch.command == DEFAULT_COPILOT_COMMAND
        assert launch.env is None

    @pytest.mark.asyncio
    async def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(cwd=str(tmp_path)))

        launch = await launch_for(adapter, "room-a")

        assert launch.cwd == str(tmp_path / "room-a")

    def test_cwd_and_a_workspace_resolver_are_exclusive(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="set either cwd or workspace_for_room"):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(cwd=str(tmp_path)),
                workspace_for_room=lambda room_id: str(tmp_path / room_id),
            )

    @pytest.mark.parametrize(
        "setting",
        [{"host": "10.0.0.5", "port": 8080}, {"port": 8080}],
        ids=["host-and-port", "port"],
    )
    def test_tcp_config_is_rejected(self, setting: dict[str, object]) -> None:
        with pytest.raises(
            ValueError,
            match="TCP ACP transport cannot guarantee room process isolation",
        ):
            CopilotACPAdapterConfig.model_validate(setting)

    def test_no_profile_uses_default_noop(self) -> None:
        # Copilot speaks vanilla ACP; the base adapter leaves profile unset and the
        # collecting client the runtime builds falls back to the no-op profile.
        adapter = CopilotACPAdapter()
        assert adapter._profile is None
        client = adapter._build_runtime()._client_factory()
        assert isinstance(client._profile, NoopACPClientProfile)

    @pytest.mark.parametrize(
        ("config", "env"),
        [
            pytest.param(
                CopilotACPAdapterConfig(github_token="ghp_x"),
                {"GITHUB_TOKEN": "ghp_x"},
                id="token",
            ),
            # Any auth Copilot supports (COPILOT_GITHUB_TOKEN, BYOK keys) passes
            # through env.
            pytest.param(
                CopilotACPAdapterConfig(
                    env={"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"}
                ),
                {"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"},
                id="env",
            ),
            pytest.param(
                CopilotACPAdapterConfig(github_token="ghp_x", env={"GH_TOKEN": "gh"}),
                {"GH_TOKEN": "gh", "GITHUB_TOKEN": "ghp_x"},
                id="token-merged-into-env",
            ),
            pytest.param(
                CopilotACPAdapterConfig(
                    github_token="from-shortcut", env={"GITHUB_TOKEN": "from-env"}
                ),
                {"GITHUB_TOKEN": "from-env"},
                id="env-wins-over-token",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_auth_reaches_the_cli_environment(
        self, config: CopilotACPAdapterConfig, env: dict[str, str], tmp_path: Path
    ) -> None:
        adapter = CopilotACPAdapter(
            config, workspace_for_room=lambda room_id: str(tmp_path / room_id)
        )

        launch = await launch_for(adapter)

        assert launch.env == env

    def test_additional_tools_are_registered(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        adapter = CopilotACPAdapter(additional_tools=[(EchoInput, _echo)])

        assert "echo" in adapter._own_tool_names


# Copilot CLI 1.0.89's efforts per model (probed live).
COPILOT_EFFORTS = {
    "claude-sonnet-5": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "claude-haiku-4.5": (),
}


def copilot(current: str = "claude-sonnet-5") -> FakeACPAgent:
    """A fake ``copilot --acp`` advertising Copilot's model catalog."""
    return (
        FakeACPAgent(supports_session_load=True)
        .advertises_models(COPILOT_EFFORTS, current=current)
        .knows_session("persisted-session")
        .will_say("Configured")
    )


@asynccontextmanager
async def copilot_room(agent: FakeACPAgent, **config: Any) -> AsyncIterator[AcpSession]:
    adapter = CopilotACPAdapter(
        CopilotACPAdapterConfig(inject_band_tools=False, **config)
    )
    async with started_acp_adapter(adapter, agent) as session:
        yield session


async def switch_room(session: AcpSession, **selection: str) -> None:
    """Switch the live session of the room ``session.send`` talks to."""
    await session.adapter.apply_model_selection(
        ModelSelection(**selection), room_id=DEFAULT_ROOM
    )


class TestCopilotACPModelSelection:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("current", ["claude-sonnet-5", "claude-haiku-4.5"])
    async def test_model_then_effort_are_set_before_the_first_prompt(
        self, current: str
    ) -> None:
        # From Haiku, the effort select only exists once gpt-5.4 is chosen.
        agent = copilot(current)

        async with copilot_room(
            agent, model="gpt-5.4", reasoning_effort="high"
        ) as session:
            reply = await session.send("Hello")

        assert reply.texts == ["Configured"]
        assert agent.config_selections() == [
            (MODEL_OPTION_ID, "gpt-5.4"),
            (EFFORT_OPTION_ID, "high"),
        ]

    @pytest.mark.asyncio
    async def test_a_restored_session_is_configured_too(self) -> None:
        agent = copilot()

        async with copilot_room(agent, model="gpt-5.4") as session:
            reply = await session.send(
                "Resume",
                bootstrap=True,
                history=ACPClientSessionState(
                    room_to_session={"room-1": "persisted-session"}
                ),
            )

        assert reply.texts == ["Configured"]
        assert agent.config_option_requests == [
            ("persisted-session", MODEL_OPTION_ID, "gpt-5.4")
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config", "set_first", "error"),
        [
            pytest.param(
                {"model": "gpt-9"},
                [],
                'model "gpt-9" is not advertised; available: '
                "claude-sonnet-5, gpt-5.4, claude-haiku-4.5",
                id="unknown-model",
            ),
            pytest.param(
                {"model": "claude-haiku-4.5", "reasoning_effort": "high"},
                [(MODEL_OPTION_ID, "claude-haiku-4.5")],
                'model "claude-haiku-4.5" offers no reasoning effort',
                id="model-without-efforts",
            ),
            pytest.param(
                {"model": "gpt-5.4", "reasoning_effort": "max"},
                [(MODEL_OPTION_ID, "gpt-5.4")],
                'reasoning effort "max" is not advertised for model "gpt-5.4"; '
                "available: none, low, medium, high, xhigh",
                id="effort-the-model-lacks",
            ),
        ],
    )
    async def test_an_unadvertised_selection_fails_the_turn_naming_the_options(
        self,
        config: dict[str, str],
        set_first: list[tuple[str, str]],
        error: str,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent, **config) as session:
            reply = await session.send("Hello")

        assert reply.errors == [CONFIG_FAILURE_PREFIX + error]
        assert agent.config_selections() == set_first
        assert agent.prompt_texts() == []

    @pytest.mark.asyncio
    async def test_a_live_room_switches_against_its_current_catalog(self) -> None:
        # Sonnet offers "high"; after switching to Haiku the room's catalog has
        # no effort select, so a stale catalog would wrongly accept "high".
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await switch_room(session, model="claude-haiku-4.5")
            with pytest.raises(BandConfigError) as rejected:
                await switch_room(session, reasoning_effort="high")

        assert str(rejected.value) == (
            'model "claude-haiku-4.5" offers no reasoning effort'
        )
        assert agent.config_selections() == [(MODEL_OPTION_ID, "claude-haiku-4.5")]

    @pytest.mark.asyncio
    async def test_a_partly_applied_switch_does_not_carry_to_later_sessions(
        self,
    ) -> None:
        # The model lands before the effort is refused; the switch raised, so
        # the room's next session starts where it would have without it.
        agent = copilot()

        async with copilot_room(agent, reasoning_effort="high") as session:
            await session.send("Hello")
            with pytest.raises(BandConfigError):
                await switch_room(session, model="gpt-5.4", reasoning_effort="max")
            await session.adapter.on_cleanup("room-1")
            await session.send("Again")

        assert agent.config_selections("fake-session-2") == [(EFFORT_OPTION_ID, "high")]

    @pytest.mark.asyncio
    async def test_an_empty_switch_leaves_the_room_on_its_configured_selection(
        self,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent, model="gpt-5.4") as session:
            await session.send("Hello")
            await agent.selects_on_its_own(
                session.session_id("room-1"), MODEL_OPTION_ID, "claude-haiku-4.5"
            )
            await switch_room(session)
            await session.adapter.on_cleanup("room-1")
            await session.send("Again")

        assert agent.config_selections("fake-session-2") == [
            (MODEL_OPTION_ID, "gpt-5.4")
        ]

    @pytest.mark.asyncio
    async def test_a_switch_follows_a_model_the_agent_chose_itself(self) -> None:
        agent = copilot()

        @agent.on_prompt
        async def pick_haiku(fake: FakeACPAgent, session_id: str) -> None:
            await fake.selects_on_its_own(
                session_id, MODEL_OPTION_ID, "claude-haiku-4.5"
            )
            await fake.say(session_id, "Switched")

        async with copilot_room(agent) as session:
            await session.send("Use haiku")
            with pytest.raises(BandConfigError) as rejected:
                await switch_room(session, reasoning_effort="high")

        assert str(rejected.value) == (
            'model "claude-haiku-4.5" offers no reasoning effort'
        )

    @pytest.mark.asyncio
    async def test_concurrent_switches_on_a_room_apply_in_call_order(self) -> None:
        # Only gpt-5.4 offers "none", so the second switch is accepted only
        # when checked against the catalog the first one left.
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            gate = agent.holds_config_replies()
            first = asyncio.create_task(switch_room(session, model="gpt-5.4"))
            await gate.received.wait()
            second = asyncio.create_task(switch_room(session, reasoning_effort="none"))
            gate.release.set()
            await asyncio.gather(first, second)

        assert agent.config_selections() == [
            (MODEL_OPTION_ID, "gpt-5.4"),
            (EFFORT_OPTION_ID, "none"),
        ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("history", "next_session"),
        [
            pytest.param(None, "fake-session-2", id="recreated"),
            pytest.param(
                ACPClientSessionState(room_to_session={"room-1": "persisted-session"}),
                "persisted-session",
                id="restored",
            ),
        ],
    )
    async def test_a_switch_outlives_the_session_it_was_made_on(
        self, history: ACPClientSessionState | None, next_session: str
    ) -> None:
        # The switch changes the model only, so the configured effort stays.
        agent = copilot()

        async with copilot_room(agent, reasoning_effort="high") as session:
            await session.send("Hello")
            await switch_room(session, model="gpt-5.4")
            await session.adapter.on_cleanup("room-1")
            await session.send("Again", bootstrap=history is not None, history=history)

        assert agent.config_selections(next_session) == [
            (MODEL_OPTION_ID, "gpt-5.4"),
            (EFFORT_OPTION_ID, "high"),
        ]

    @pytest.mark.asyncio
    async def test_a_switch_to_a_model_without_efforts_drops_the_configured_effort(
        self,
    ) -> None:
        # Haiku offers no effort, so the room's next session must not ask for
        # the configured "high".
        agent = copilot()

        async with copilot_room(
            agent, model="gpt-5.4", reasoning_effort="high"
        ) as session:
            await session.send("Hello")
            await switch_room(session, model="claude-haiku-4.5")
            await session.adapter.on_cleanup("room-1")
            reply = await session.send("Again")

        assert reply.errors == []
        assert agent.config_selections("fake-session-2") == [
            (MODEL_OPTION_ID, "claude-haiku-4.5")
        ]

    @pytest.mark.asyncio
    async def test_a_remembered_switch_the_agent_refuses_fails_one_turn_only(
        self,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await switch_room(session, model="gpt-5.4")
            await session.adapter.on_cleanup("room-1")
            agent.advertises_models(
                {"claude-sonnet-5": COPILOT_EFFORTS["claude-sonnet-5"]},
                current="claude-sonnet-5",
            )
            refused = await session.send("Again")
            recovered = await session.send("And again")

        assert refused.errors == [
            CONFIG_FAILURE_PREFIX
            + 'model "gpt-5.4" is not advertised; available: claude-sonnet-5'
        ]
        assert recovered.texts == ["Configured"]

    @pytest.mark.asyncio
    async def test_a_remembered_switch_survives_a_set_the_agent_never_answered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(session_config, "SESSION_CONFIG_TIMEOUT_SECONDS", 0.05)
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await switch_room(session, model="gpt-5.4")
            await session.adapter.on_cleanup("room-1")
            gate = agent.holds_config_replies()
            timed_out = await session.send("Again")
            gate.release.set()
            await session.adapter.on_cleanup("room-1")
            await session.send("And again")

        assert "did not respond" in timed_out.errors[0]
        assert agent.config_selections("fake-session-3")[0] == (
            MODEL_OPTION_ID,
            "gpt-5.4",
        )

    @pytest.mark.asyncio
    async def test_a_switch_refused_before_any_change_leaves_the_room_unpinned(
        self,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            with pytest.raises(BandConfigError):
                await switch_room(session, model="gpt-9")
            await session.adapter.on_cleanup("room-1")
            await session.send("Again")

        assert agent.config_selections("fake-session-2") == []

    @pytest.mark.asyncio
    async def test_a_switch_queued_behind_a_room_cleanup_names_the_ended_session(
        self,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            gate = agent.holds_config_replies()
            first = asyncio.create_task(switch_room(session, model="gpt-5.4"))
            await gate.received.wait()
            queued = asyncio.create_task(switch_room(session, model="claude-haiku-4.5"))
            await asyncio.sleep(0)  # one tick: the queued switch parks on the lock
            await session.adapter.on_cleanup("room-1")
            results = await asyncio.gather(first, queued, return_exceptions=True)

        assert isinstance(results[1], BandConfigError)
        assert str(results[1]) == "room room-1's ACP session ended mid-switch"

    @pytest.mark.asyncio
    async def test_a_cleanup_at_any_point_of_a_switch_names_the_ended_session(
        self,
    ) -> None:
        # Sweeps where the cleanup lands: before the model reply, between the
        # model and effort steps, and after the switch completes.
        outcomes = {await self._switch_cleaned_up_after(ticks) for ticks in range(12)}

        assert outcomes <= {"ok", "room room-1's ACP session ended mid-switch"}
        assert "room room-1's ACP session ended mid-switch" in outcomes

    @staticmethod
    async def _switch_cleaned_up_after(ticks: int) -> str:
        agent = copilot()
        async with copilot_room(agent) as session:
            await session.send("Hello")
            gate = agent.holds_config_replies()
            switch = asyncio.create_task(
                switch_room(session, model="gpt-5.4", reasoning_effort="high")
            )
            await gate.received.wait()
            gate.release.set()
            for _ in range(ticks):
                await asyncio.sleep(0)
            await session.adapter.on_cleanup(DEFAULT_ROOM)
            (result,) = await asyncio.gather(switch, return_exceptions=True)
        return "ok" if result is None else str(result)

    @pytest.mark.asyncio
    async def test_a_setup_that_loses_its_connection_retries_on_the_next_turn(
        self,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await switch_room(session, model="gpt-5.4")
            await session.adapter.on_cleanup(DEFAULT_ROOM)
            agent.hangs_up_on_next_config_option()
            lost = await session.send("Again")
            healed = await session.send("And again")

        assert lost.errors == [CONFIG_FAILURE_PREFIX + "Connection closed"]
        assert healed.texts == ["Configured"]
        assert agent.config_selections("fake-session-3") == [
            (MODEL_OPTION_ID, "gpt-5.4"),
            (EFFORT_OPTION_ID, "medium"),
        ]

    @pytest.mark.asyncio
    async def test_a_switch_leaves_other_rooms_on_the_configured_selection(
        self,
    ) -> None:
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await switch_room(session, model="gpt-5.4")
            await session.send("Hi", room="room-2")

        assert agent.config_selections("fake-session-2") == []

    @pytest.mark.asyncio
    async def test_a_switch_survives_an_adapter_restart(self) -> None:
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await switch_room(session, model="gpt-5.4")
            await session.adapter.cleanup_all()
            await session.adapter.on_started("Fake Agent", "restarted")
            await session.send("Again")

        assert agent.config_selections("fake-session-2") == [
            (MODEL_OPTION_ID, "gpt-5.4"),
            (EFFORT_OPTION_ID, "medium"),
        ]

    @pytest.mark.asyncio
    async def test_switching_a_room_without_a_live_session_is_refused(self) -> None:
        async with copilot_room(copilot()) as session:
            with pytest.raises(BandConfigError, match="no live ACP session"):
                await switch_room(session, model="gpt-5.4")

    def test_typed_selection_and_a_resolver_are_exclusive(self) -> None:
        async def resolver(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {}

        with pytest.raises(ValueError, match="not both"):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(model="gpt-5.4"),
                resolve_session_config=resolver,
            )
