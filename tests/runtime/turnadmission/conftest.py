"""Fixtures for message-turn admission tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from band.platform.link import BandLink
from band.runtime.cycle import TurnScope
from band.runtime.execution import ExecutionContext
from band.runtime.types import SessionConfig
from tests.runtime.helpers import (
    AGENT_ID,
    LifecyclePlatform,
    ResponseGate,
    rest_client_over,
    run_through,
)
from tests.runtime.turnadmission.kit import TurnRecorder


@pytest.fixture
def peer() -> LifecyclePlatform:
    return LifecyclePlatform()


@pytest.fixture
def adapter() -> TurnRecorder:
    return TurnRecorder()


@pytest.fixture
def setup_gate(peer: LifecyclePlatform) -> ResponseGate:
    """Holds the participants load of a turn's setup once ``release`` clears."""
    gate = ResponseGate(peer, "/participants")
    gate.release.set()
    return gate


@pytest_asyncio.fixture(loop_scope="function")
async def ctx(
    adapter: TurnRecorder, setup_gate: ResponseGate
) -> AsyncIterator[ExecutionContext]:
    async with rest_client_over(setup_gate.answer) as rest:
        link = BandLink(agent_id=AGENT_ID, api_key="test-key")
        link.rest = rest
        ctx = run_through(
            adapter,
            link,
            config=SessionConfig(
                enable_context_hydration=False, enable_working_state=False
            ),
        )
        try:
            yield ctx
        finally:
            setup_gate.release.set()
            adapter.release.set()
            await ctx.stop()


@pytest_asyncio.fixture(loop_scope="function")
async def stop_observer(ctx: ExecutionContext) -> AsyncIterator[asyncio.Event]:
    """A previous turn's platform-stop observer that runs until released.

    The next turn waits up to one cleanup grace for it before it begins.
    """
    release = asyncio.Event()
    observer = asyncio.create_task(release.wait())
    ctx.current_scope = TurnScope(ctx._control_revision, observer_task=observer)
    try:
        yield release
    finally:
        release.set()
        await observer
