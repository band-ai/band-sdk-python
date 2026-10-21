"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
import pytest_asyncio

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    AskFactory,
    BandMCPCalls,
    FakeOpencodeClient,
    batch_input,
    deliver_turn,
    make_platform_message,
)
from tests.adapters.opencode.ownership import AdapterOwner
from tests.mcpbackends import BackendStarts, backends_created_by


@pytest.fixture
def asks() -> AskFactory:
    """Builds OpenCode permission and question asks for a test."""
    return AskFactory()


@pytest.fixture
def tools() -> FakeAgentTools:
    """Fresh platform tools for one adapter test."""
    return FakeAgentTools()


@pytest_asyncio.fixture(loop_scope="function")
async def make_adapter(
    fake_band_mcp_backends: BackendStarts,
) -> AsyncIterator[Callable[..., OpencodeAdapter]]:
    """Build an adapter around the scenario's fake OpenCode client."""

    async with AdapterOwner() as adapters:
        yield adapters.create


@pytest.fixture(autouse=True)
def fake_band_mcp_backends() -> Iterator[BackendStarts]:
    """Fake every Band MCP backend start in OpenCode adapter tests."""
    with backends_created_by() as starts:
        yield starts


@pytest.fixture
def mcp_backend(fake_band_mcp_backends: BackendStarts) -> BandMCPCalls:
    """Band tool calls the model makes over the adapter's MCP backend."""
    return BandMCPCalls(fake_band_mcp_backends)


ParkRoom = Callable[..., Awaitable[OpencodeAdapter]]


@pytest.fixture
def park_room(
    make_adapter: Callable[..., OpencodeAdapter], tools: FakeAgentTools
) -> ParkRoom:
    """Start an adapter and deliver a first turn over ``messages`` (one plain
    message by default) through the runtime's own entry points, returning once
    the turn finishes or an ask in ``client``'s script releases it to wait on
    the room. ``make_adapter`` closes every adapter it built."""

    async def park(
        client: FakeOpencodeClient,
        *messages: PlatformMessage,
        config: OpencodeAdapterConfig | None = None,
    ) -> OpencodeAdapter:
        adapter = make_adapter(client, config=config)
        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = messages or (make_platform_message(content="run it"),)
        await deliver_turn(adapter, batch_input(first_turn, tools))
        return adapter

    return park
