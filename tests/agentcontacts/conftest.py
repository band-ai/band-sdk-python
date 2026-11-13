"""Fixtures for Agent contact event wiring tests."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from tests.agentcontacts.kit import AGENT_API_URL, STAMP, ContactAdapter
from tests.identifiers import AGENT_ID


@pytest.fixture
def contact_adapter() -> ContactAdapter:
    """Exercise contact wiring with the adapter's real construction and hooks."""
    return ContactAdapter()


@pytest.fixture
def platform_rest(httpx_mock: HTTPXMock) -> None:
    """Answer the REST calls an agent in no rooms makes while it starts."""
    httpx_mock.add_response(
        method="GET",
        url=f"{AGENT_API_URL}/me",
        json={
            "data": {
                "id": AGENT_ID,
                "name": "Contact Agent",
                "handle": "owner/contact-agent",
                "description": "Handles contacts",
                "owner_uuid": "user-1",
                "feature_flags": {},
                "inserted_at": STAMP,
                "updated_at": STAMP,
            }
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{AGENT_API_URL}/chats?page=1&page_size=100",
        json={"data": [], "metadata": {"page": 1, "total_pages": 1}},
    )
