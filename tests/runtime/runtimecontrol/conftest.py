"""Fixtures for AgentRuntime control routing tests."""

from __future__ import annotations

import pytest

from band.platform.link import BandLink
from band.testing.platform import platform_connection_stub
from tests.runtime.runtimecontrol.kit import (
    ControlledRooms,
)


@pytest.fixture
def link() -> BandLink:
    """A real link that is never connected: control routing needs no I/O."""
    platform = platform_connection_stub()
    return BandLink(
        agent_id=platform.agent_id,
        api_key=platform.api_key,
        ws_url=platform.ws_url,
        rest_url=platform.rest_url,
    )


@pytest.fixture
def rooms(link: BandLink) -> ControlledRooms:
    return ControlledRooms(link, "r1", "r2")
