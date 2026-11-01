"""Native body fixtures with confirmed worker and bridge cleanup."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from band.runtime.cycle import TurnScope
from band.runtime.tools.agent import AgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.agno.native.bodies import NativeBody
from tests.adapters.agno.native.kit import SyncBody


@pytest.fixture
def native_scope() -> TurnScope:
    scope = TurnScope(control_revision=0)
    scope.admit()
    return scope


@pytest.fixture
def native_recorder() -> ActivityRecorder:
    return ActivityRecorder()


@pytest.fixture
def native_tools(
    native_scope: TurnScope, native_recorder: ActivityRecorder
) -> AgentTools:
    tools = AgentTools("native-room", MagicMock())
    tools._scope = native_scope
    native_recorder.observing(tools)
    return tools


@pytest.fixture
def native_body() -> NativeBody:
    return NativeBody()


@pytest_asyncio.fixture(loop_scope="function")
async def body(scoped_tools: AgentTools) -> AsyncIterator[SyncBody]:
    body = SyncBody()
    try:
        yield body
    finally:
        body.release.set()
        if body.entered.is_set():
            await body.finished.wait()


@pytest_asyncio.fixture(loop_scope="function")
async def scoped_tools(native_tools: AgentTools) -> AsyncIterator[AgentTools]:
    try:
        yield native_tools
    finally:
        assert native_tools._scope is not None
        owners = tuple(native_tools._scope.workers)
        if owners:
            await asyncio.gather(*(owner.task for owner in owners))
