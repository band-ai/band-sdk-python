"""A contact event routed to the hub room is also posted there as a task event."""

from __future__ import annotations

from pytest_httpx import HTTPXMock

from band.client.streaming import ContactRequestReceivedPayload
from band.platform.event import ContactRequestReceivedEvent
from band.runtime.contact_handler import ContactEventHandler
from tests.runtime.contactwire.kit import (
    AGENT_API_URL,
    HUB_ROOM_ID,
    STAMP,
    HubRoom,
    sent,
)

EVENTS_URL = f"{AGENT_API_URL}/chats/{HUB_ROOM_ID}/events"
REQUEST = ContactRequestReceivedEvent(
    payload=ContactRequestReceivedPayload(
        id="request-1",
        from_handle="jane_smith",
        from_name="Jane Smith",
        message="Let's work together",
        status="pending",
        inserted_at=STAMP,
    )
)
REQUEST_TEXT = (
    "[Contact Request] Jane Smith (@jane_smith) wants to connect.\n"
    'Message: "Let\'s work together"\n'
    "Request ID: request-1"
)
CREATE_HUB_ROOM = ("POST", f"{AGENT_API_URL}/chats", {"chat": {}})
POST_TASK_EVENT = (
    "POST",
    EVENTS_URL,
    {
        "event": {
            "content": REQUEST_TEXT,
            "message_type": "task",
            "metadata": {"contact_event_type": "contact_request_received"},
        }
    },
)


class TestHubRoomTaskEvent:
    async def test_contact_event_is_injected_and_posted_as_a_task(
        self, hub_handler: ContactEventHandler, hub_room: HubRoom, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=EVENTS_URL,
            json={"data": {"id": "event-1", "message_type": "task", "success": True}},
        )

        await hub_handler.handle(REQUEST)

        assert hub_room.received == [(HUB_ROOM_ID, REQUEST_TEXT)]
        assert [sent(r) for r in httpx_mock.get_requests()] == [
            CREATE_HUB_ROOM,
            POST_TASK_EVENT,
        ]

    async def test_a_failed_task_post_still_settles_the_event(
        self, hub_handler: ContactEventHandler, hub_room: HubRoom, httpx_mock: HTTPXMock
    ) -> None:
        """The injection is what the agent acts on; a redelivery is a duplicate."""
        httpx_mock.add_response(
            method="POST",
            url=EVENTS_URL,
            status_code=422,
            json={
                "error": {
                    "code": "validation_error",
                    "message": "content is invalid",
                    "request_id": "request-trace-1",
                }
            },
        )

        await hub_handler.handle(REQUEST)
        await hub_handler.handle(REQUEST)

        assert hub_room.received == [(HUB_ROOM_ID, REQUEST_TEXT)]
        assert [sent(r) for r in httpx_mock.get_requests()] == [
            CREATE_HUB_ROOM,
            POST_TASK_EVENT,
        ]
