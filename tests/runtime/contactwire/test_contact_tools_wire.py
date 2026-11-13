"""ContactTools sends the platform's contact requests and reads its real responses."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from band.runtime.contact_tools import ContactTools
from tests.runtime.contactwire.kit import AGENT_API_URL, STAMP, sent

CONTACTS_URL = f"{AGENT_API_URL}/contacts"
# ContactTools renders the parsed timestamp with str(datetime).
STAMP_TEXT = "2026-02-09 10:30:00+00:00"


class TestContactToolsWire:
    async def test_list_contacts_reads_each_contact_and_the_page(
        self, tools: ContactTools, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            json={
                "data": [
                    {
                        "id": "contact-1",
                        "handle": "jane_smith",
                        "name": "Jane Smith",
                        "type": "User",
                        "online": True,
                        "inserted_at": STAMP,
                    },
                    {
                        "id": "contact-2",
                        "handle": "jane_smith/weather",
                        "name": "Weather",
                        "type": "Agent",
                        "description": "Forecasts",
                        "is_external": False,
                        "tags": ["weather"],
                        "inserted_at": STAMP,
                    },
                ],
                "metadata": {
                    "page": 2,
                    "page_size": 2,
                    "total_count": 4,
                    "total_pages": 2,
                },
            }
        )

        result = await tools.list_contacts(page=2, page_size=2)

        assert sent(httpx_mock.get_request()) == (
            "GET",
            f"{CONTACTS_URL}?page=2&page_size=2",
            None,
        )
        assert result == {
            "contacts": [
                {
                    "id": "contact-1",
                    "handle": "jane_smith",
                    "name": "Jane Smith",
                    "type": "User",
                },
                {
                    "id": "contact-2",
                    "handle": "jane_smith/weather",
                    "name": "Weather",
                    "type": "Agent",
                },
            ],
            "metadata": {
                "page": 2,
                "page_size": 2,
                "total_count": 4,
                "total_pages": 2,
            },
        }

    async def test_add_contact_sends_the_request_and_reads_its_status(
        self, tools: ContactTools, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json={"data": {"id": "request-1", "status": "pending"}})

        result = await tools.add_contact("@jane_smith", message="Let's work together")

        assert sent(httpx_mock.get_request()) == (
            "POST",
            f"{CONTACTS_URL}/add",
            {"handle": "@jane_smith", "message": "Let's work together"},
        )
        assert result == {"id": "request-1", "status": "pending"}

    async def test_remove_contact_by_handle_sends_only_the_handle(
        self, tools: ContactTools, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(json={"data": {"status": "removed"}})

        result = await tools.remove_contact(handle="@jane_smith")

        assert sent(httpx_mock.get_request()) == (
            "POST",
            f"{CONTACTS_URL}/remove",
            {"handle": "@jane_smith"},
        )
        assert result == {"status": "removed"}

    async def test_list_contact_requests_reads_both_directions(
        self, tools: ContactTools, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            json={
                "data": {
                    "received": [
                        {
                            "id": "request-1",
                            "from_handle": "jane_smith",
                            "from_name": "Jane Smith",
                            "message": "Let's work together",
                            "status": "pending",
                            "inserted_at": STAMP,
                        }
                    ],
                    "sent": [
                        {
                            "id": "request-2",
                            "to_handle": "john_doe/planner",
                            "to_name": "Planner",
                            "status": "pending",
                            "inserted_at": STAMP,
                        }
                    ],
                },
                "metadata": {
                    "page": 1,
                    "page_size": 50,
                    "received": {"total": 1, "total_pages": 1},
                    "sent": {"total": 1, "total_pages": 1},
                },
            }
        )

        result = await tools.list_contact_requests()

        assert sent(httpx_mock.get_request()) == (
            "GET",
            f"{CONTACTS_URL}/requests?page=1&page_size=50&sent_status=pending",
            None,
        )
        assert result == {
            "received": [
                {
                    "id": "request-1",
                    "from_handle": "jane_smith",
                    "from_name": "Jane Smith",
                    "message": "Let's work together",
                    "status": "pending",
                    "inserted_at": STAMP_TEXT,
                }
            ],
            "sent": [
                {
                    "id": "request-2",
                    "to_handle": "john_doe/planner",
                    "to_name": "Planner",
                    "message": None,
                    "status": "pending",
                    "inserted_at": STAMP_TEXT,
                }
            ],
            "metadata": {
                "page": 1,
                "page_size": 50,
                "received": {"total": 1, "total_pages": 1},
                "sent": {"total": 1, "total_pages": 1},
            },
        }

    @pytest.mark.parametrize(
        ("action", "status"), [("approve", "approved"), ("reject", "rejected")]
    )
    async def test_respond_contact_request_sends_the_action_by_request_id(
        self, tools: ContactTools, httpx_mock: HTTPXMock, action: str, status: str
    ) -> None:
        httpx_mock.add_response(json={"data": {"id": "request-1", "status": status}})

        result = await tools.respond_contact_request(action, request_id="request-1")

        assert sent(httpx_mock.get_request()) == (
            "POST",
            f"{CONTACTS_URL}/requests/respond",
            {"action": action, "request_id": "request-1"},
        )
        assert result == {"id": "request-1", "status": status}
