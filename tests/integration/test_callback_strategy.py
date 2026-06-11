"""Integration tests for CALLBACK strategy.

These tests verify the ContactEventHandler with CALLBACK strategy against
simulated contact events. The callbacks use ContactTools to interact with
the real API.

Run with: uv run pytest tests/integration/test_callback_strategy.py -v -s
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock

from band.platform.event import (
    ContactRequestReceivedEvent,
    ContactAddedEvent,
)
from band.client.streaming import (
    ContactRequestReceivedPayload,
    ContactAddedPayload,
)
from band.runtime.contact_handler import ContactEventHandler
from band.runtime.contact_tools import ContactTools
from band.runtime.types import ContactEventConfig, ContactEventStrategy
from tests.integration.conftest import requires_api, requires_multi_agent

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

    # Agent 1: Remove contact with Agent 2 if exists
    try:
        await api_client.agent_api_contacts.remove_agent_contact(handle=agent2_handle)
    except Exception:
        pass

    # Agent 2: Remove contact with Agent 1 if exists
    try:
        await api_client_2.agent_api_contacts.remove_agent_contact(handle=agent1_handle)
    except Exception:
        pass

    # Cancel/reject any pending requests
    try:
        await api_client.agent_api_contacts.respond_to_agent_contact_request(
            action="cancel", handle=agent2_handle
        )
    except Exception:
        pass

    try:
        await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
            action="cancel", handle=agent1_handle
        )
    except Exception:
        pass

    # Reject any received requests
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
    except Exception:
        pass

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
    except Exception:
        pass

    await asyncio.sleep(0.3)


@requires_multi_agent
class TestCallbackAutoApprove:
    """Test CALLBACK strategy with auto-approve logic."""

    async def test_auto_approve_callback(self, api_client, api_client_2):
        """CALLBACK auto-approves contact request via ContactTools.

        Flow:
        1. Set up CALLBACK handler with auto-approve logic
        2. Agent 2 sends contact request to Agent 1 (via REST)
        3. Simulate the WebSocket event arriving at Agent 1
        4. Handler calls callback which approves via ContactTools
        5. Verify contact is established
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: CALLBACK auto-approve")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        logger.info("Agent 1 (receiver): %s", agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle
        logger.info("Agent 2 (sender): %s", agent2_handle)

        # Track if callback was invoked
        callback_invoked = False
        callback_event = None

        async def auto_approve_callback(event: Any, tools: ContactTools) -> None:
            nonlocal callback_invoked, callback_event
            callback_invoked = True
            callback_event = event

            if isinstance(event, ContactRequestReceivedEvent):
                logger.info(
                    "Callback received request from %s", event.payload.from_handle
                )
                # Auto-approve the request using ContactTools
                result = await tools.respond_contact_request(
                    "approve", request_id=event.payload.id
                )
                logger.info("Callback approved, result: %s", result)

        # Create handler with CALLBACK strategy
        mock_link = MagicMock()
        mock_link.rest = api_client

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=auto_approve_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        try:
            # Agent 2 sends contact request to Agent 1
            logger.info("\n--- Agent 2 sending request ---")
            await api_client_2.agent_api_contacts.add_agent_contact(
                handle=agent1_handle,
                message="Auto-approve test request",
            )
            await asyncio.sleep(0.5)

            # Get the pending request to simulate the WebSocket event
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
                # The request might have already been auto-approved if they were contacts
                logger.info("No pending request found - may have been auto-approved")
                return

            # Simulate WebSocket event
            logger.info("\n--- Simulating WebSocket event ---")
            event = ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id=pending_request.id,
                    from_handle=getattr(pending_request, "from_handle", agent2_handle),
                    from_name=getattr(pending_request, "from_name", "Agent 2"),
                    message=getattr(pending_request, "message", None),
                    status="pending",
                    inserted_at=str(
                        getattr(pending_request, "inserted_at", "2024-01-01T00:00:00Z")
                    ),
                )
            )
            await handler.handle(event)

            # Verify callback was invoked
            assert callback_invoked, "Callback should have been invoked"
            assert callback_event is event

            # Verify contact was established
            await asyncio.sleep(0.5)
            contacts = await api_client.agent_api_contacts.list_agent_contacts()
            contact_handles = [
                getattr(c, "handle", None) for c in (contacts.data or [])
            ]
            assert agent2_handle in contact_handles, (
                f"Agent 2 ({agent2_handle}) should be in Agent 1's contacts"
            )

            logger.info("\nSUCCESS: Auto-approve callback worked")

        finally:
            await cleanup_contact_state(api_client, api_client_2)


@requires_multi_agent
class TestCallbackAutoReject:
    """Test CALLBACK strategy with auto-reject logic."""

    async def test_auto_reject_callback(self, api_client, api_client_2):
        """CALLBACK auto-rejects contact request via ContactTools."""
        logger.info("\n" + "=" * 60)
        logger.info("Testing: CALLBACK auto-reject")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle

        callback_invoked = False

        async def auto_reject_callback(event: Any, tools: ContactTools) -> None:
            nonlocal callback_invoked
            callback_invoked = True

            if isinstance(event, ContactRequestReceivedEvent):
                logger.info(
                    "Callback rejecting request from %s", event.payload.from_handle
                )
                result = await tools.respond_contact_request(
                    "reject", request_id=event.payload.id
                )
                logger.info("Callback rejected, result: %s", result)

        mock_link = MagicMock()
        mock_link.rest = api_client

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=auto_reject_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        try:
            # Agent 2 sends contact request
            logger.info("\n--- Agent 2 sending request ---")
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

            # Simulate WebSocket event
            logger.info("\n--- Simulating WebSocket event ---")
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

            assert callback_invoked, "Callback should have been invoked"

            # Verify no contact was established
            await asyncio.sleep(0.5)
            contacts = await api_client.agent_api_contacts.list_agent_contacts()
            contact_handles = [
                getattr(c, "handle", None) for c in (contacts.data or [])
            ]
            assert agent2_handle not in contact_handles, (
                "Agent 2 should NOT be in Agent 1's contacts after rejection"
            )

            logger.info("\nSUCCESS: Auto-reject callback worked")

        finally:
            await cleanup_contact_state(api_client, api_client_2)


@requires_api
class TestCallbackWithLogging:
    """Test CALLBACK strategy with logging/tracking."""

    async def test_callback_with_logging(self, api_client):
        """CALLBACK can log and track events without failing."""
        logger.info("\n" + "=" * 60)
        logger.info("Testing: CALLBACK with logging")
        logger.info("=" * 60)

        logged_events: list[Any] = []

        async def logging_callback(event: Any, tools: ContactTools) -> None:
            logged_events.append(event)
            logger.info("Event logged: %s", type(event).__name__)

        mock_link = MagicMock()
        mock_link.rest = api_client

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=logging_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        # Simulate multiple events
        events = [
            ContactRequestReceivedEvent(
                payload=ContactRequestReceivedPayload(
                    id="req-1",
                    from_handle="@test1",
                    from_name="Test 1",
                    message=None,
                    status="pending",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            ),
            ContactAddedEvent(
                payload=ContactAddedPayload(
                    id="contact-1",
                    handle="@test2",
                    name="Test 2",
                    type="User",
                    inserted_at="2024-01-01T00:00:00Z",
                )
            ),
        ]

        for event in events:
            await handler.handle(event)

        assert len(logged_events) == 2
        assert isinstance(logged_events[0], ContactRequestReceivedEvent)
        assert isinstance(logged_events[1], ContactAddedEvent)

        logger.info("\nSUCCESS: Logging callback tracked %d events", len(logged_events))


@requires_api
class TestCallbackErrorRecovery:
    """Test CALLBACK strategy error recovery."""

    async def test_callback_error_recovery(self, api_client):
        """Callback error doesn't break the handler."""
        logger.info("\n" + "=" * 60)
        logger.info("Testing: CALLBACK error recovery")
        logger.info("=" * 60)

        call_count = 0

        async def failing_callback(event: Any, tools: ContactTools) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("Simulated callback failure")
            # Second call succeeds
            logger.info("Second callback succeeded")

        mock_link = MagicMock()
        mock_link.rest = api_client

        config = ContactEventConfig(
            strategy=ContactEventStrategy.CALLBACK,
            on_event=failing_callback,
        )
        handler = ContactEventHandler(config, mock_link)

        event1 = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-fail",
                from_handle="@failing",
                from_name="Failing",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )

        event2 = ContactRequestReceivedEvent(
            payload=ContactRequestReceivedPayload(
                id="req-success",
                from_handle="@success",
                from_name="Success",
                message=None,
                status="pending",
                inserted_at="2024-01-01T00:00:00Z",
            )
        )

        # First call fails but doesn't crash
        await handler.handle(event1)

        # Second call succeeds
        await handler.handle(event2)

        assert call_count == 2, "Both events should have been processed"
        logger.info("\nSUCCESS: Handler recovered from callback error")
