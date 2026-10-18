"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager

import pytest
import pytest_asyncio

from band.adapters.opencode import OpencodeAdapter
from band.testing import FakeAgentTools
from tests.adapters.opencode.approvalclock import ApprovalTurn, pending_approval
from tests.adapters.opencode.helpers import AskFactory, BandMCPCalls
from tests.adapters.opencode.ownership import AdapterOwner
from tests.mcpbackends import BackendStarts, backends_created_by


@pytest.fixture
def approval_turn() -> Callable[[], AbstractAsyncContextManager[ApprovalTurn]]:
    return pending_approval


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
