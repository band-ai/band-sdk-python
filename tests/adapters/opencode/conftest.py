"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import pytest
from typing_extensions import Unpack

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import FeatureKwargs, PlatformMessage
from band.runtime.custom_tools import CustomToolDef
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import (
    AskFactory,
    BandMCPCalls,
    FakeOpencodeClient,
    batch_input,
    deliver_turn,
    make_platform_message,
)
from tests.mcpbackends import BackendStarts, backends_created_by


@pytest.fixture
def asks() -> AskFactory:
    """Builds OpenCode permission and question asks for a test."""
    return AskFactory()


@pytest.fixture
def tools() -> FakeAgentTools:
    """Fresh platform tools for one adapter test."""
    return FakeAgentTools()


@pytest.fixture
def make_adapter() -> Callable[..., OpencodeAdapter]:
    """Build an adapter around the scenario's fake OpenCode client."""

    def build(
        client: FakeOpencodeClient,
        *,
        config: OpencodeAdapterConfig | None = None,
        additional_tools: list[CustomToolDef] | None = None,
        **features: Unpack[FeatureKwargs],
    ) -> OpencodeAdapter:
        return OpencodeAdapter(
            config=config,
            additional_tools=additional_tools,
            client_factory=lambda _: client,
            **features,
        )

    return build


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
async def park_room(
    make_adapter: Callable[..., OpencodeAdapter], tools: FakeAgentTools
) -> AsyncIterator[ParkRoom]:
    """Start an adapter and deliver a first turn over ``messages`` (one plain
    message by default) through the runtime's own entry points, returning once
    the turn finishes or an ask in ``client``'s script releases it to wait on
    the room."""
    adapters: list[OpencodeAdapter] = []

    async def park(
        client: FakeOpencodeClient,
        *messages: PlatformMessage,
        config: OpencodeAdapterConfig | None = None,
    ) -> OpencodeAdapter:
        adapter = make_adapter(client, config=config)
        adapters.append(adapter)
        await adapter.on_started("OpenCode Agent", "A coding agent")
        first_turn = messages or (make_platform_message(content="run it"),)
        await deliver_turn(adapter, batch_input(first_turn, tools))
        return adapter

    yield park
    for adapter in adapters:
        await adapter.on_cleanup("room-1")
