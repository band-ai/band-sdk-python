"""How ``running_provisioned_agent`` hands a bespoke run its execution policy and its turn observer.

The platform connection is the un-runnable boundary here, so ``running_agent`` is
replaced by a stand-in that records what it was started with; the adapter is real,
and so are the observer and its turns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.toolkit import provisioning
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import (
    ProvisionedAgent,
    running_provisioned_agent,
)
from tests.framework_conformance.invocationkit import (
    ROOM_ID,
    DecliningAdapter,
    original,
    turn_input,
)

IDENTITY = ProvisionedAgent(id="agent-1", api_key="key", name="agent")
DEADLINE_S = 5.0


class IdleResources:
    """Just enough ``ResourceManager`` to provision the one identity."""

    settings = None

    async def provision_agent(self, label: str) -> ProvisionedAgent:
        return IDENTITY


@pytest.fixture
def started_with(monkeypatch: pytest.MonkeyPatch) -> list[SessionConfig | None]:
    """The session config of each agent started."""
    configs: list[SessionConfig | None] = []

    @asynccontextmanager
    async def running_agent(
        provisioned: ProvisionedAgent,
        adapter: Any,
        settings: Any,
        session_config: SessionConfig | None = None,
    ) -> AsyncIterator[ProvisionedAgent]:
        configs.append(session_config)
        yield provisioned

    monkeypatch.setattr(provisioning, "running_agent", running_agent)
    return configs


class TestProvisionedAgentRun:
    async def test_the_agent_starts_under_the_requested_session_config(
        self, started_with: list[SessionConfig | None]
    ) -> None:
        batching = SessionConfig(consume_all_queued=True)

        async with running_provisioned_agent(
            DecliningAdapter(), IdleResources(), session_config=batching
        ):
            pass

        assert started_with == [batching]

    async def test_the_turns_of_the_adapter_are_observed(
        self, started_with: list[SessionConfig | None]
    ) -> None:
        adapter, observer = (
            DecliningAdapter(),
            InvocationObserver(deadline_s=DEADLINE_S),
        )

        async with running_provisioned_agent(
            adapter, IdleResources(), observer=observer
        ):
            await adapter.on_messages(turn_input(original("m1"), original("m2")))

        assert observer.partition(ROOM_ID) == [("m1", "m2")]
