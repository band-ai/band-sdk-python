"""Fixtures for Agent contact event wiring tests."""

from __future__ import annotations

from datetime import datetime

import pytest
from band_rest import AgentMe, GetAgentMeResponse
from pytest_httpx import HTTPXMock

from band.runtime.tools import CHAT_PAGE_SIZE
from tests.agentcontacts.kit import ContactAdapter
from tests.fakeplatform import AGENT_API_URL, CHATS_URL, STAMP
from tests.identifiers import AGENT_ID

REGISTERED = datetime.fromisoformat(STAMP)
AGENT_ME = GetAgentMeResponse(
    data=AgentMe(
        id=AGENT_ID,
        name="Contact Agent",
        handle="owner/contact-agent",
        description="Handles contacts",
        owner_uuid="user-1",
        feature_flags={},
        inserted_at=REGISTERED,
        updated_at=REGISTERED,
    )
)


@pytest.fixture
def contact_adapter() -> ContactAdapter:
    """Exercise contact wiring with the adapter's real construction and hooks."""
    return ContactAdapter()


@pytest.fixture
def platform_rest(httpx_mock: HTTPXMock) -> None:
    """Answer the REST calls an agent in no rooms makes while it starts."""
    httpx_mock.add_response(
        method="GET", url=f"{AGENT_API_URL}/me", json=AGENT_ME.model_dump(mode="json")
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{CHATS_URL}?page=1&page_size={CHAT_PAGE_SIZE}",
        json={"data": [], "metadata": {"page": 1, "total_pages": 1}},
    )
