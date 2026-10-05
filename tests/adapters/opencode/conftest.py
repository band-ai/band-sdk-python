"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from typing_extensions import Unpack

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import FeatureKwargs
from band.runtime.custom_tools import CustomToolDef
from band.testing import FakeAgentTools
from tests.adapters.opencode.helpers import AskFactory, BandMCPCalls, FakeOpencodeClient
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
