"""Shared fixtures for the ParlantAdapter tests.

Parlant itself is faked through ``sys.modules`` so these run in every venv;
the real engine is covered by ``tests/integrations/parlant`` and the E2E smoke.
"""

from __future__ import annotations

import sys
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.core.types import PlatformMessage
from band.testing import FakeAgentTools
from tests.adapters.parlant.helpers import AI_AGENT_SOURCE, MESSAGE_KIND

StartAdapter = Callable[..., Awaitable[ParlantAdapter]]


@pytest.fixture
def sample_message() -> PlatformMessage:
    return PlatformMessage(
        id="msg-123",
        room_id="room-123",
        content="Hello, agent!",
        sender_id="user-456",
        sender_type="User",
        sender_name="Alice",
        message_type="text",
        metadata={},
        created_at=datetime.now(UTC),
    )


@pytest.fixture
def mock_tools() -> FakeAgentTools:
    """The room's tools, recording what the adapter posted."""
    return FakeAgentTools()


@pytest.fixture
def mock_app() -> MagicMock:
    """The server's Parlant Application; no agent response by default."""
    app = MagicMock()
    app.sessions = AsyncMock()
    app.sessions.create = AsyncMock(return_value=MagicMock(id="session-123"))
    app.sessions.create_customer_message = AsyncMock(return_value=MagicMock(offset=1))
    app.sessions.create_event = AsyncMock()
    app.sessions.wait_for_more_events = AsyncMock(return_value=False)
    app.sessions.find_events = AsyncMock(return_value=[])
    return app


@pytest.fixture
def application_class(mock_app: MagicMock) -> Iterator[MagicMock]:
    """Fake ``parlant.core.application`` so on_started's import resolves."""
    application = MagicMock(name="Application")
    with patch.dict(
        sys.modules,
        {"parlant.core.application": MagicMock(Application=application)},
    ):
        yield application


@pytest.fixture
def parlant_sessions() -> Iterator[None]:
    """Fake the Parlant session modules a turn imports."""
    with patch.dict(
        sys.modules,
        {
            "parlant.core.app_modules.sessions": MagicMock(
                Moderation=MagicMock(NONE="none")
            ),
            "parlant.core.sessions": MagicMock(
                EventKind=MagicMock(MESSAGE=MESSAGE_KIND),
                EventSource=MagicMock(CUSTOMER="customer", AI_AGENT=AI_AGENT_SOURCE),
            ),
            "parlant.core.async_utils": MagicMock(Timeout=lambda x: x),
        },
    ):
        yield


@pytest.fixture
def mock_parlant_server(mock_app: MagicMock, application_class: MagicMock) -> MagicMock:
    """A Parlant server whose container holds ``mock_app``."""
    server = MagicMock()
    server.container = {application_class: mock_app}
    server.create_customer = AsyncMock(return_value=MagicMock(id="customer-123"))
    created_agent = MagicMock()
    created_agent.id = "parlant-agent-created"
    created_agent.create_guideline = AsyncMock()
    server.create_agent = AsyncMock(return_value=created_agent)
    return server


@pytest.fixture
def mock_parlant_agent() -> MagicMock:
    agent = MagicMock()
    agent.id = "parlant-agent-123"
    agent.name = "TestBot"
    agent.create_guideline = AsyncMock()
    return agent


@pytest.fixture
def start_adapter(
    mock_parlant_server: MagicMock, mock_parlant_agent: MagicMock
) -> StartAdapter:
    """Start an adapter on the borrowed mock server and agent."""

    async def start(config: ParlantAdapterConfig | None = None) -> ParlantAdapter:
        adapter = ParlantAdapter(
            config or ParlantAdapterConfig(response_timeout=0.05, response_poll=0.01),
            server=mock_parlant_server,
            parlant_agent=mock_parlant_agent,
        )
        await adapter.on_started(agent_name="TestBot", agent_description="A test bot")
        return adapter

    return start


@pytest.fixture(autouse=True)
def stub_band_tools() -> Iterator[MagicMock]:
    """Stub the Band->Parlant tool build in on_started.

    The real ``create_parlant_tools`` imports ``parlant.sdk``; doing that mid-suite,
    after other tests have patched ``parlant.core.*`` into ``sys.modules``, corrupts
    beartype's import hook and breaks later genuine parlant imports.
    """
    with patch(
        "band.adapters.parlant.adapter.create_parlant_tools", return_value=[]
    ) as stub:
        yield stub
