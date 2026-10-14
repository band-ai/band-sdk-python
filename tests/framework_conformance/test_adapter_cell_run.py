"""How ``AdapterCell.run_as`` hands a run its execution policy and its turn observer.

The platform connection is the un-runnable boundary here, so ``running_agent`` is
replaced by a stand-in that records what it was started with; the adapter the cell
builds is real, and so are the observer and its holds.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

import pytest

from band.runtime.sessionconfig import SessionConfig
from tests.e2e.baseline.toolkit import provisioning
from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ProvisionedAgent
from tests.framework_conformance.invocationkit import (
    ROOM_ID,
    DecliningAdapter,
    original,
    turn_input,
)

IDENTITY = ProvisionedAgent(id="agent-1", api_key="key", name="agent")
DEADLINE_S = 5.0


class IdleResources:
    """Just enough ``ResourceManager`` for ``run_as``: no overlapping-run guard to trip."""

    @contextmanager
    def track_running(self, agent_id: str) -> Iterator[None]:
        yield


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> DecliningAdapter:
    """The adapter every cell in the test builds."""
    built = DecliningAdapter()
    monkeypatch.setattr(provisioning, "build_adapter", lambda *args, **kwargs: built)
    return built


@pytest.fixture
def started_with(monkeypatch: pytest.MonkeyPatch) -> list[SessionConfig | None]:
    """The session config of each agent a cell starts."""
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


def cell() -> AdapterCell:
    return AdapterCell(adapter_id="anthropic", settings=None, resources=IdleResources())


class TestAdapterCellRun:
    async def test_the_agent_starts_under_the_requested_session_config(
        self, adapter: DecliningAdapter, started_with: list[SessionConfig | None]
    ) -> None:
        batching = SessionConfig(consume_all_queued=True)

        async with cell().run_as(IDENTITY, session_config=batching):
            pass

        assert started_with == [batching]

    async def test_the_turns_of_the_adapter_the_cell_built_are_observed(
        self, adapter: DecliningAdapter, started_with: list[SessionConfig | None]
    ) -> None:
        observer = InvocationObserver(deadline_s=DEADLINE_S)

        async with cell().run_as(IDENTITY, observer=observer):
            await adapter.on_messages(turn_input(original("m1"), original("m2")))

        assert observer.partition(ROOM_ID) == [("m1", "m2")]

    async def test_a_failing_run_frees_a_parked_turn_before_the_agent_stops(
        self, adapter: DecliningAdapter, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observer = InvocationObserver(deadline_s=DEADLINE_S)
        parked_turn: list[asyncio.Task[None]] = []

        @asynccontextmanager
        async def stopping_agent(
            provisioned: ProvisionedAgent,
            adapter: Any,
            settings: Any,
            session_config: SessionConfig | None = None,
        ) -> AsyncIterator[ProvisionedAgent]:
            try:
                yield provisioned
            finally:
                # Stopping an agent waits for the turn it is running.
                await asyncio.wait_for(parked_turn[0], timeout=1)

        monkeypatch.setattr(provisioning, "running_agent", stopping_agent)

        with pytest.raises(RuntimeError, match="the test failed"):
            async with cell().run_as(IDENTITY, observer=observer):
                hold = observer.hold_next(ROOM_ID)
                parked_turn.append(
                    asyncio.create_task(adapter.on_messages(turn_input(original("m1"))))
                )
                await hold.entered()
                raise RuntimeError("the test failed")

        assert adapter.built == [("m1",)]
