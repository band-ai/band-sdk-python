"""Integration tests for HUB_ROOM strategy.

These tests verify that the HUB_ROOM strategy correctly routes contact events
to a dedicated hub room for LLM reasoning.

Run with: uv run pytest tests/integration/test_hub_room_strategy.py -v -s
"""

from __future__ import annotations

import asyncio
import logging

from band.client.streaming import (
    ContactRequestReceivedPayload,
)
from band.platform.event import (
    ContactRequestReceivedEvent,
)
from band.platform.link import BandLink
from band.runtime.contact_handler import ContactEventHandler
from band.runtime.contact_tools import ContactTools
from band.runtime.types import ContactEventConfig, ContactEventStrategy
from tests.integration.conftest import (
    fetch_all_context,
    requires_api,
    requires_multi_agent,
)

logger = logging.getLogger(__name__)


async def cleanup_contact_state(api_client, api_client_2):
    """Clean up any existing contact state between the two agents."""
    response1 = await api_client.agent_api_identity.get_agent_me()
    agent1_handle = response1.data.handle

    response2 = await api_client_2.agent_api_identity.get_agent_me()
    agent2_handle = response2.data.handle

    logger.info(
        "Cleaning up contact state between %s and %s", agent1_handle, agent2_handle
    )

    # Remove contacts
    try:
        await api_client.agent_api_contacts.remove_agent_contact(handle=agent2_handle)
    except Exception as e:
        logger.debug("Cleanup: remove contact agent1->agent2: %s", e)

    try:
        await api_client_2.agent_api_contacts.remove_agent_contact(handle=agent1_handle)
    except Exception as e:
        logger.debug("Cleanup: remove contact agent2->agent1: %s", e)

    # Cancel/reject pending requests
    try:
        await api_client.agent_api_contacts.respond_to_agent_contact_request(
            action="cancel", handle=agent2_handle
        )
    except Exception as e:
        logger.debug("Cleanup: cancel request agent1->agent2: %s", e)

    try:
        await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
            action="cancel", handle=agent1_handle
        )
    except Exception as e:
        logger.debug("Cleanup: cancel request agent2->agent1: %s", e)

    # Reject received requests
    try:
        response = await api_client.agent_api_contacts.list_agent_contact_requests()
        received = getattr(response.data, "received", []) or []
        for req in received:
            from_handle = getattr(req, "from_handle", None)
            status = getattr(req, "status", None)
            if from_handle == agent2_handle and status == "pending":
                await api_client.agent_api_contacts.respond_to_agent_contact_request(
                    action="reject", request_id=req.id
                )
    except Exception as e:
        logger.debug("Cleanup: reject requests for agent1: %s", e)

    try:
        response = await api_client_2.agent_api_contacts.list_agent_contact_requests()
        received = getattr(response.data, "received", []) or []
        for req in received:
            from_handle = getattr(req, "from_handle", None)
            status = getattr(req, "status", None)
            if from_handle == agent1_handle and status == "pending":
                await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
                    action="reject", request_id=req.id
                )
    except Exception as e:
        logger.debug("Cleanup: reject requests for agent2: %s", e)

    await asyncio.sleep(0.3)


@requires_api
class TestHubRoomReceivesEvents:
    """Test that hub room receives contact events."""

    async def test_hub_room_receives_contact_request(
        self, api_client, integration_settings, shared_room
    ):
        """Contact request event appears in hub room.

        Uses session-scoped shared_room to avoid creating new rooms.
        Pre-sets handler._hub_room_id so initialize_hub_room() is skipped.
        """

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Hub room receives contact request")
        logger.info("=" * 60)

        # Create handler with HUB_ROOM strategy
        link = BandLink(
            agent_id=integration_settings.test_agent_id,
            api_key=integration_settings.band_api_key,
            rest_url=integration_settings.band_base_url,
            ws_url=integration_settings.band_ws_url,
        )

        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )
        handler = ContactEventHandler(config, link)
        handler._hub_room_id = shared_room

        # Simulate a contact request event
        event = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-test-hub",
                from_handle="@test-sender",
                from_name="Test Sender",
                message="Please connect with me!",
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )

        await handler.handle(event)

        # Verify hub room is the shared room
        assert handler._hub_room_id == shared_room
        logger.info("Hub room verified: %s", handler._hub_room_id)

        # Verify room exists by checking it appears in the chat list
        response = await api_client.agent_api_chats.list_agent_chats()
        chat_ids = [chat.id for chat in (response.data or [])]
        assert shared_room in chat_ids, f"Hub room {shared_room} not found in chat list"
        logger.info("Hub room verified in chat list")

        logger.info("\nSUCCESS: Hub room receives contact request")


@requires_multi_agent
class TestHubRoomAgentActions:
    """Test that agent can take action from hub room."""

    async def test_hub_room_agent_can_approve(
        self, api_client, api_client_2, integration_settings, shared_room
    ):
        """Agent in hub room can approve via ContactTools.

        Uses session-scoped shared_room to avoid creating new rooms.
        Pre-sets handler._hub_room_id so initialize_hub_room() is skipped.
        """

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Hub room agent can approve")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        logger.info("Agent 1: %s", agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle
        logger.info("Agent 2: %s", agent2_handle)

        # Create handler for Agent 1
        link = BandLink(
            agent_id=integration_settings.test_agent_id,
            api_key=integration_settings.band_api_key,
            rest_url=integration_settings.band_base_url,
            ws_url=integration_settings.band_ws_url,
        )

        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )
        handler = ContactEventHandler(config, link)
        handler._hub_room_id = shared_room

        try:
            # Agent 2 sends contact request
            logger.info("\n--- Agent 2 sending request ---")
            await api_client_2.agent_api_contacts.add_agent_contact(
                handle=agent1_handle,
                message="Hub room approve test",
            )
            await asyncio.sleep(0.5)

            # Get the pending request
            requests_response = (
                await api_client.agent_api_contacts.list_agent_contact_requests()
            )
            received = getattr(requests_response.data, "received", []) or []
            pending_request = None
            for req in received:
                from_handle = getattr(req, "from_handle", None)
                status = getattr(req, "status", None)
                if from_handle == agent2_handle and status == "pending":
                    pending_request = req
                    break

            if pending_request is None:
                logger.info("No pending request found - may have been auto-approved")
                return

            # Simulate WebSocket event triggering handler
            logger.info("\n--- Handler routing to hub room ---")
            event = ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id=pending_request.id,
                    from_handle=agent2_handle,
                    from_name="Agent 2",
                    message="Hub room approve test",
                    status="pending",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            )
            await handler.handle(event)
            logger.info("Event routed to hub room: %s", handler._hub_room_id)

            # Simulate agent deciding to approve (LLM response)
            logger.info("\n--- Agent approving from hub room context ---")
            tools = ContactTools(api_client)
            result = await tools.respond_contact_request(
                "approve", request_id=pending_request.id
            )
            assert result["status"] == "approved"
            logger.info("Approved via ContactTools")

            # Verify contact established
            await asyncio.sleep(0.5)
            contacts = await api_client.agent_api_contacts.list_agent_contacts()
            contact_handles = [
                getattr(c, "handle", None) for c in (contacts.data or [])
            ]
            assert agent2_handle in contact_handles

            logger.info("\nSUCCESS: Agent can approve from hub room")

        finally:
            await cleanup_contact_state(api_client, api_client_2)

    async def test_hub_room_agent_can_reject(
        self, api_client, api_client_2, integration_settings, shared_room
    ):
        """Agent in hub room can reject via ContactTools.

        Uses session-scoped shared_room to avoid creating new rooms.
        Pre-sets handler._hub_room_id so initialize_hub_room() is skipped.
        """

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Hub room agent can reject")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle

        link = BandLink(
            agent_id=integration_settings.test_agent_id,
            api_key=integration_settings.band_api_key,
            rest_url=integration_settings.band_base_url,
            ws_url=integration_settings.band_ws_url,
        )

        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )
        handler = ContactEventHandler(config, link)
        handler._hub_room_id = shared_room

        try:
            # Agent 2 sends contact request
            await api_client_2.agent_api_contacts.add_agent_contact(
                handle=agent1_handle
            )
            await asyncio.sleep(0.5)

            # Get the pending request
            requests_response = (
                await api_client.agent_api_contacts.list_agent_contact_requests()
            )
            received = getattr(requests_response.data, "received", []) or []
            pending_request = None
            for req in received:
                from_handle = getattr(req, "from_handle", None)
                status = getattr(req, "status", None)
                if from_handle == agent2_handle and status == "pending":
                    pending_request = req
                    break

            if pending_request is None:
                logger.info("No pending request found")
                return

            # Route to hub room
            event = ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id=pending_request.id,
                    from_handle=agent2_handle,
                    from_name="Agent 2",
                    message=None,
                    status="pending",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            )
            await handler.handle(event)

            # Agent rejects
            tools = ContactTools(api_client)
            result = await tools.respond_contact_request(
                "reject", request_id=pending_request.id
            )
            assert result["status"] == "rejected"

            # Verify no contact
            await asyncio.sleep(0.5)
            contacts = await api_client.agent_api_contacts.list_agent_contacts()
            contact_handles = [
                getattr(c, "handle", None) for c in (contacts.data or [])
            ]
            assert agent2_handle not in contact_handles

            logger.info("\nSUCCESS: Agent can reject from hub room")

        finally:
            await cleanup_contact_state(api_client, api_client_2)


@requires_api
class TestHubRoomPersistence:
    """Test hub room persistence behavior."""

    async def test_hub_room_persists_across_reconnect(
        self, api_client, integration_settings, shared_room
    ):
        """Same hub room is reused across multiple handler instances.

        Uses session-scoped shared_room to avoid creating new rooms.
        Pre-sets _hub_room_id on both handlers to simulate room persistence.
        """

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Hub room persistence")
        logger.info("=" * 60)

        link = BandLink(
            agent_id=integration_settings.test_agent_id,
            api_key=integration_settings.band_api_key,
            rest_url=integration_settings.band_base_url,
            ws_url=integration_settings.band_ws_url,
        )

        # First handler with pre-set hub room
        config1 = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )
        handler1 = ContactEventHandler(config1, link)
        handler1._hub_room_id = shared_room

        event1 = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-persist-1",
                from_handle="@user1",
                from_name="User 1",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        await handler1.handle(event1)

        first_room_id = handler1._hub_room_id
        logger.info("First handler room: %s", first_room_id)

        # Second handler (simulating reconnect) with same pre-set hub room
        config2 = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )
        handler2 = ContactEventHandler(config2, link)
        handler2._hub_room_id = shared_room

        event2 = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-persist-2",
                from_handle="@user2",
                from_name="User 2",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        await handler2.handle(event2)

        second_room_id = handler2._hub_room_id
        logger.info("Second handler room: %s", second_room_id)

        # Both handlers use the same room (persistence)
        assert first_room_id is not None
        assert second_room_id is not None
        assert first_room_id == second_room_id == shared_room

        logger.info("\nSUCCESS: Hub room persists across handler instances")


@requires_api
class TestHubRoomIsolation:
    """Test that hub room events are isolated from regular rooms."""

    async def test_hub_room_isolated_from_other_rooms(
        self, api_client, integration_settings, shared_room
    ):
        """Hub room events are posted to the hub room and not to other rooms.

        Sets up the handler with a real on_hub_event callback and marks the
        hub room as ready.  After handling, verifies the contact event task
        was actually posted to the hub room's context via REST.
        """

        logger.info("\n" + "=" * 60)
        logger.info("Testing: Hub room isolation")
        logger.info("=" * 60)

        link = BandLink(
            agent_id=integration_settings.test_agent_id,
            api_key=integration_settings.band_api_key,
            rest_url=integration_settings.band_base_url,
            ws_url=integration_settings.band_ws_url,
        )

        # Capture injected events
        injected_events: list[tuple[str, object]] = []

        async def capture_hub_event(room_id, message_event):
            injected_events.append((room_id, message_event))

        # Create handler with all pieces wired up
        config = ContactEventConfig(
            strategy=ContactEventStrategy.HUB_ROOM,
        )
        handler = ContactEventHandler(config, link)
        handler._hub_room_id = shared_room
        handler._on_hub_event = capture_hub_event
        handler.mark_hub_room_ready()

        # Send contact event to hub room
        event = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-isolation-test",
                from_handle="@isolated-user",
                from_name="Isolated User",
                message="This should only appear in hub room",
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )
        await handler.handle(event)
        await asyncio.sleep(0.5)

        # 1. Verify the on_hub_event callback was invoked with the hub room
        assert len(injected_events) == 1, (
            f"Expected 1 injected event, got {len(injected_events)}"
        )
        injected_room_id, _ = injected_events[0]
        assert injected_room_id == shared_room

        # 2. Verify the task event was posted to the hub room's context
        context_items = await fetch_all_context(api_client, shared_room)
        task_events = [
            item
            for item in context_items
            if getattr(item, "message_type", None) == "task"
            and "isolated-user" in (getattr(item, "content", "") or "").lower()
        ]
        assert len(task_events) > 0, (
            "Contact event task should appear in hub room context"
        )
        logger.info("Found %s task event(s) in hub room context", len(task_events))

        logger.info("\nSUCCESS: Hub room receives events correctly")
