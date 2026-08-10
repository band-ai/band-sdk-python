"""What a deployment serves, and what the SDK does when it serves less."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from band.agent import Agent
from band.core.simple_adapter import SimpleAdapter
from band.core.types import AdapterFeatures, Capability
from band.runtime.capabilities import (
    PLATFORM_CAPABILITY_FLAGS,
    capabilities_the_platform_refuses,
)


class RecordingAdapter(SimpleAdapter[Any]):
    """Remembers the capability set it was started with."""

    SUPPORTED_CAPABILITIES = frozenset({Capability.FILES, Capability.MEMORY})

    def __init__(self, features: AdapterFeatures) -> None:
        super().__init__(features=features)
        self.capabilities_at_start: frozenset[Capability] | None = None

    async def on_started(self, agent_name: str, agent_description: str) -> None:
        await super().on_started(agent_name, agent_description)
        self.capabilities_at_start = self.features.capabilities

    async def on_message(self, *args: Any, **kwargs: Any) -> None: ...


def make_agent(
    *, capabilities: set[Capability], platform_flags: dict[str, bool] | None
) -> tuple[Agent, RecordingAdapter]:
    adapter = RecordingAdapter(AdapterFeatures(capabilities=capabilities))
    runtime = AsyncMock()
    runtime.agent_name = "Tester"
    runtime.agent_description = "Asks what the deployment serves"
    runtime.platform_feature_flags = platform_flags
    return Agent(runtime=runtime, adapter=adapter), adapter  # type: ignore[arg-type]


class TestReadingWhatThePlatformServes:
    """One SDK build meets three kinds of node, so it has to ask.

    A SaaS node, an on-prem node granted room files and an on-prem node
    without them all answer ``GET /api/v1/agent/me``; only the payload differs.
    The flag keys are the platform's own — the same ``ff_*`` names the SPA and
    JAM read — so all three clients share one vocabulary.
    """

    def test_a_capability_the_deployment_turned_off_is_refused(self) -> None:
        refused = capabilities_the_platform_refuses({"ff_file_transfer": False})

        assert refused == frozenset({Capability.FILES})

    def test_a_capability_the_deployment_serves_is_not_refused(self) -> None:
        assert capabilities_the_platform_refuses({"ff_file_transfer": True}) == frozenset()

    def test_a_platform_that_never_answered_refuses_nothing(self) -> None:
        """Silence is not a "no", and treating it as one breaks working agents.

        A platform predating the capability block still serves the file
        endpoints. Reading its silence as a refusal would strip the tools from
        an agent whose deployment works today, so only an explicit ``false``
        counts.
        """
        assert capabilities_the_platform_refuses(None) == frozenset()

    def test_a_platform_that_never_heard_of_the_capability_refuses_nothing(self) -> None:
        assert capabilities_the_platform_refuses({"ff_block_user": True}) == frozenset()

    def test_every_sdk_capability_the_platform_can_gate_names_its_flag(self) -> None:
        # The flag key is typed once here and nowhere else; a second spelling
        # elsewhere would fail silently as "capability off".
        assert PLATFORM_CAPABILITY_FLAGS[Capability.FILES] == "ff_file_transfer"


class TestDroppingRefusedCapabilities:
    def test_a_refused_capability_leaves_the_others_alone(self) -> None:
        features = AdapterFeatures(
            capabilities={Capability.FILES, Capability.MEMORY},
            emit=(),
            exclude_tools=("band_send_event",),
        )

        pruned = features.without_capabilities({Capability.FILES})

        assert pruned.capabilities == frozenset({Capability.MEMORY})
        assert pruned.exclude_tools == ("band_send_event",)

    def test_dropping_nothing_returns_an_equal_set(self) -> None:
        features = AdapterFeatures(capabilities={Capability.FILES})

        assert features.without_capabilities(frozenset()).capabilities == frozenset(
            {Capability.FILES}
        )


class TestAgentStartupHonoursThePlatform:
    """The operator's opt-in says "this agent may use files"; it cannot say
    "this deployment has them". When the two disagree the deployment wins, and
    it has to win before the adapter builds its tool list — an agent that
    advertises a tool the platform will 404 spends real model turns finding out.
    """

    @pytest.mark.asyncio
    async def test_a_deployment_without_files_takes_the_file_tools_away(self) -> None:
        agent, adapter = make_agent(
            capabilities={Capability.FILES, Capability.MEMORY},
            platform_flags={"ff_file_transfer": False},
        )

        await agent.start()

        assert adapter.features.capabilities == frozenset({Capability.MEMORY})

    @pytest.mark.asyncio
    async def test_a_deployment_with_files_keeps_them(self) -> None:
        agent, adapter = make_agent(
            capabilities={Capability.FILES},
            platform_flags={"ff_file_transfer": True},
        )

        await agent.start()

        assert Capability.FILES in adapter.features.capabilities

    @pytest.mark.asyncio
    async def test_a_platform_that_says_nothing_changes_nothing(self) -> None:
        agent, adapter = make_agent(
            capabilities={Capability.FILES}, platform_flags=None
        )

        await agent.start()

        assert Capability.FILES in adapter.features.capabilities

    @pytest.mark.asyncio
    async def test_the_adapter_sees_the_pruned_set_when_it_starts(self) -> None:
        # Ordering is the whole point: claude_sdk builds its MCP tool list
        # inside on_started, so a prune that lands afterwards changes nothing.
        agent, adapter = make_agent(
            capabilities={Capability.FILES},
            platform_flags={"ff_file_transfer": False},
        )

        await agent.start()

        assert adapter.capabilities_at_start == frozenset()
