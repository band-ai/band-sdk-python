"""Tests for CopilotACPAdapter.

CopilotACPAdapter is a thin specialization of ACPClientAdapter — its contract is
how a CopilotACPAdapterConfig maps onto the base adapter's transport, auth, and
system-context wiring. Runtime/on_started behavior is covered by the generic ACP
client suite (tests/integrations/acp/); the model-selection tests drive the
adapter against an in-process fake shaped like Copilot's live catalog.
"""

from __future__ import annotations

import logging
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
from band.integrations.acp.client_adapter import ACPClientAdapter
from band.integrations.acp.client_profiles import NoopACPClientProfile
from band.integrations.acp.client_types import ACPClientSessionState
from band.integrations.acp.session_config import ACPConfigError, ACPConfigRequest
from tests.integrations.acp.acp_toolkit.agent import FakeACPAgent
from tests.integrations.acp.acp_toolkit.harness import AcpSession, started_acp_adapter


class TestCopilotACPAdapterConstruction:
    def test_is_acp_client_adapter(self) -> None:
        assert issubclass(CopilotACPAdapter, ACPClientAdapter)

    def test_defaults_to_stdio_copilot_command(self) -> None:
        adapter = CopilotACPAdapter()
        assert adapter._command == list(DEFAULT_COPILOT_COMMAND)

    def test_no_config_equivalent_to_default_config(self) -> None:
        a, b = CopilotACPAdapter(), CopilotACPAdapter(CopilotACPAdapterConfig())
        for attr in ("_command", "_env", "_inject_band_tools"):
            assert getattr(a, attr) == getattr(b, attr)

    def test_custom_command_is_forwarded(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(command=("copilot", "--acp", "--yolo"))
        )
        assert adapter._command == ["copilot", "--acp", "--yolo"]

    def test_cwd_becomes_a_room_workspace_root(self, tmp_path: Path) -> None:
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(cwd=str(tmp_path)))

        assert adapter._workspace("room-a") == str(tmp_path / "room-a")

    def test_no_profile_uses_default_noop(self) -> None:
        # Copilot speaks vanilla ACP; the base adapter leaves profile unset and the
        # collecting client the runtime builds falls back to the no-op profile.
        adapter = CopilotACPAdapter()
        assert adapter._profile is None
        client = adapter._build_runtime()._client_factory()
        assert isinstance(client._profile, NoopACPClientProfile)

    def test_github_token_injected_into_stdio_env(self) -> None:
        adapter = CopilotACPAdapter(CopilotACPAdapterConfig(github_token="ghp_x"))
        assert adapter._env == {"GITHUB_TOKEN": "ghp_x"}

    def test_no_env_without_token(self) -> None:
        # No token and no env → rely on the CLI's ambient login (stored / gh / BYOK).
        assert CopilotACPAdapter()._env is None

    def test_env_passthrough_for_any_auth_method(self) -> None:
        # A user can auth however Copilot supports — e.g. the highest-precedence
        # COPILOT_GITHUB_TOKEN, or BYOK provider keys — via the general env passthrough.
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(env={"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"})
        )
        assert adapter._env == {"COPILOT_GITHUB_TOKEN": "tok", "OTHER": "x"}

    def test_github_token_convenience_merges_with_env(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(github_token="ghp_x", env={"GH_TOKEN": "gh"})
        )
        assert adapter._env == {"GH_TOKEN": "gh", "GITHUB_TOKEN": "ghp_x"}

    def test_explicit_env_github_token_wins_over_convenience(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(
                github_token="from-shortcut", env={"GITHUB_TOKEN": "from-env"}
            )
        )
        assert adapter._env == {"GITHUB_TOKEN": "from-env"}

    def test_custom_section_threaded_to_system_context(self) -> None:
        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(custom_section="You are a triage bot.")
        )
        assert adapter._custom_section == "You are a triage bot."

    def test_inject_band_tools_forwarded(self) -> None:
        assert CopilotACPAdapter()._inject_band_tools is True
        assert (
            CopilotACPAdapter(
                CopilotACPAdapterConfig(inject_band_tools=False)
            )._inject_band_tools
            is False
        )

    def test_additional_tools_forwarded(self) -> None:
        class EchoInput(BaseModel):
            text: str

        def _echo(text: str) -> str:
            return text

        tool = (EchoInput, _echo)
        adapter = CopilotACPAdapter(additional_tools=[tool])
        assert adapter._custom_tools == [tool]

    def test_session_config_resolver_is_forwarded(self) -> None:
        async def resolver(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {"reasoning_effort": "high"}

        adapter = CopilotACPAdapter(
            CopilotACPAdapterConfig(resolve_session_config=resolver)
        )

        assert adapter._resolve_session_config is resolver


class TestCopilotACPAdapterTcpTransport:
    def test_tcp_config_is_rejected(self) -> None:
        with pytest.raises(
            ValueError,
            match="TCP ACP transport cannot guarantee room process isolation",
        ):
            CopilotACPAdapter(CopilotACPAdapterConfig(host="10.0.0.5", port=8080))

    def test_custom_command_with_tcp_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not both"):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(
                    command=("copilot", "--acp", "--yolo"), host="10.0.0.5", port=8080
                )
            )

    def test_tcp_with_auth_warns_before_rejection(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # CopilotACPAdapter warns about ignored auth before the base adapter rejects TCP.
        with (
            caplog.at_level(logging.WARNING, logger="band.adapters.copilot_acp"),
            pytest.raises(
                ValueError,
                match="TCP ACP transport cannot guarantee room process isolation",
            ),
        ):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(
                    host="10.0.0.5", port=8080, github_token="ghp_x"
                )
            )
        assert any("ignored over TCP" in r.message for r in caplog.records)


# Copilot CLI 1.0.89's efforts per model (probed live): selecting a model
# replaces the effort select, or drops it for a model without efforts.
COPILOT_EFFORTS = {
    "claude-sonnet-5": ("low", "medium", "high", "xhigh", "max"),
    "gpt-5.4": ("none", "low", "medium", "high", "xhigh"),
    "claude-haiku-4.5": (),
}
CONFIG_FAILED = "ACP session configuration failed: "


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
            ("model", "gpt-5.4"),
            ("reasoning_effort", "high"),
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
            ("persisted-session", "model", "gpt-5.4")
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
                [("model", "claude-haiku-4.5")],
                'model "claude-haiku-4.5" offers no reasoning effort',
                id="model-without-efforts",
            ),
            pytest.param(
                {"model": "gpt-5.4", "reasoning_effort": "max"},
                [("model", "gpt-5.4")],
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

        assert reply.errors == [CONFIG_FAILED + error]
        assert agent.config_selections() == set_first
        assert agent.prompt_texts() == []

    @pytest.mark.asyncio
    async def test_a_live_room_switches_against_its_current_catalog(self) -> None:
        # Sonnet offers "high"; after switching to Haiku the room's catalog has
        # no effort select, so a stale catalog would wrongly accept "high".
        agent = copilot()

        async with copilot_room(agent) as session:
            await session.send("Hello")
            await session.adapter.apply_model_selection(
                ModelSelection(model="claude-haiku-4.5"), room_id="room-1"
            )
            with pytest.raises(ACPConfigError) as rejected:
                await session.adapter.apply_model_selection(
                    ModelSelection(reasoning_effort="high"), room_id="room-1"
                )

        assert str(rejected.value) == (
            'model "claude-haiku-4.5" offers no reasoning effort'
        )
        assert agent.config_selections() == [("model", "claude-haiku-4.5")]

    @pytest.mark.asyncio
    async def test_switching_a_room_without_a_live_session_is_refused(self) -> None:
        async with copilot_room(copilot()) as session:
            with pytest.raises(BandConfigError, match="no live ACP session"):
                await session.adapter.apply_model_selection(
                    ModelSelection(model="gpt-5.4"), room_id="room-1"
                )

    def test_typed_selection_and_a_resolver_are_exclusive(self) -> None:
        async def resolver(request: ACPConfigRequest) -> dict[str, str]:
            del request
            return {}

        with pytest.raises(ValueError, match="not both"):
            CopilotACPAdapter(
                CopilotACPAdapterConfig(
                    model="gpt-5.4", resolve_session_config=resolver
                )
            )
