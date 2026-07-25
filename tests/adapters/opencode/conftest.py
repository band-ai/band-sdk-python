"""Pytest fixtures for OpenCode adapter tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.core.types import AdapterFeatures
from band.runtime.custom_tools import CustomToolDef
from band.testing import FakeAgentTools

from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    make_fake_mcp_backend_factory,
)


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
        features: AdapterFeatures | None = None,
    ) -> OpencodeAdapter:
        return OpencodeAdapter(
            config=config,
            additional_tools=additional_tools,
            client_factory=lambda _: client,
            features=features,
        )

    return build


@pytest.fixture(autouse=True)
def patch_mcp_backend() -> Any:
    """Patch MCP backend creation for every OpenCode adapter test."""
    with patch(
        "band.adapters.opencode.adapter.create_band_mcp_backend",
        make_fake_mcp_backend_factory(),
    ):
        yield
