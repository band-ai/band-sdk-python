"""Integration tests for contact WebSocket events.

These tests verify the full contact flow with real WebSocket events:
1. Agent 1 sends contact request to Agent 2
2. Agent 2 receives contact_request_received event
3. Agent 2 approves/rejects the request
4. Both agents receive contact_request_updated event
5. On approval, both receive contact_added event
6. On removal, both receive contact_removed event

Setup:
- Agent 1: Primary test agent (BAND_API_KEY)
- Agent 2: Secondary test agent (BAND_API_KEY_2)

Run with: uv run pytest tests/integration/test_contact_websocket.py -v -s
"""

import asyncio
import logging

import pytest

from band.client.streaming import (
    WebSocketClient,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
    ContactAddedPayload,
    ContactRemovedPayload,
)
from tests.integration.conftest import requires_multi_agent

logger = logging.getLogger(__name__)


async def cleanup_contact_state(api_client, api_client_2):
    """Clean up any existing contact state between the two agents.

    This ensures tests start from a clean state by:
    1. Removing any existing contact between the agents
    2. Rejecting/canceling any pending contact requests
    """
    # Get agent identities
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
        logger.info("  Agent 1 removed contact with Agent 2")
    except Exception:
        pass  # Contact doesn't exist

    # Agent 2: Remove contact with Agent 1 if exists
    try:
        await api_client_2.agent_api_contacts.remove_agent_contact(handle=agent1_handle)
        logger.info("  Agent 2 removed contact with Agent 1")
    except Exception:
        pass  # Contact doesn't exist

    # Agent 1: Cancel any SENT pending requests to Agent 2
    try:
        await api_client.agent_api_contacts.respond_to_agent_contact_request(
            action="cancel",
            handle=agent2_handle,
        )
        logger.info("  Agent 1 canceled pending request to Agent 2")
    except Exception as e:
        logger.debug("  No sent request to cancel from Agent 1: %s", e)

    # Agent 2: Cancel any SENT pending requests to Agent 1
    try:
        await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
            action="cancel",
            handle=agent1_handle,
        )
        logger.info("  Agent 2 canceled pending request to Agent 1")
    except Exception as e:
        logger.debug("  No sent request to cancel from Agent 2: %s", e)

    # Agent 1: Reject any RECEIVED pending requests from Agent 2
    try:
        response = await api_client.agent_api_contacts.list_agent_contact_requests()
        received = getattr(response.data, "received", []) or []
        for req in received:
            from_handle = getattr(req, "from_handle", None)
            status = getattr(req, "status", None)
            if from_handle == agent2_handle and status == "pending":
                await api_client.agent_api_contacts.respond_to_agent_contact_request(
                    action="reject",
                    request_id=req.id,
                )
                logger.info("  Agent 1 rejected pending request from Agent 2")
    except Exception as e:
        logger.debug("  No received requests to reject at Agent 1: %s", e)

    # Agent 2: Reject any RECEIVED pending requests from Agent 1
    try:
        response = await api_client_2.agent_api_contacts.list_agent_contact_requests()
        received = getattr(response.data, "received", []) or []
        for req in received:
            from_handle = getattr(req, "from_handle", None)
            status = getattr(req, "status", None)
            if from_handle == agent1_handle and status == "pending":
                await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
                    action="reject",
                    request_id=req.id,
                )
                logger.info("  Agent 2 rejected pending request from Agent 1")
    except Exception as e:
        logger.debug("  No received requests to reject at Agent 2: %s", e)

    await asyncio.sleep(0.3)  # Allow state to settle
    logger.info("Cleanup complete")


@requires_multi_agent
class TestContactWebSocketEvents:
    """Test contact WebSocket events with real multi-agent flow."""

    async def test_contact_request_received_event(
        self, api_client, api_client_2, integration_settings
    ):
        """Test that Agent 2 receives contact_request_received when Agent 1 sends request.

        Flow:
        1. Clean up any existing contact state
        2. Agent 2 subscribes to contacts channel
        3. Agent 1 sends contact request to Agent 2
        4. Agent 2 should receive contact_request_received event
        5. Clean up
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: contact_request_received WebSocket event")
        logger.info("=" * 60)

        # Clean up first
        await cleanup_contact_state(api_client, api_client_2)

        # Get agent identities
        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        agent1_name = response1.data.name
        logger.info("Agent 1: %s (%s)", agent1_name, agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_id = response2.data.id
        agent2_handle = response2.data.handle
        agent2_name = response2.data.name
        logger.info("Agent 2: %s (%s)", agent2_name, agent2_handle)

        # Track received events
        request_received = asyncio.Event()
        received_payload: ContactRequestReceivedPayload | None = None

        async def on_contact_request_received(p: ContactRequestReceivedPayload):
            nonlocal received_payload
            logger.info(
                "[Agent 2 WS] Received contact_request_received: %s from %s",
                p.id,
                p.from_handle,
            )
            received_payload = p
            request_received.set()

        async def noop(p):
            pass

        # Agent 2 connects and subscribes to contacts channel
        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key_2,
            agent_id=agent2_id,
        )

        try:
            async with ws:
                await ws.join_agent_contacts_channel(
                    agent2_id,
                    on_contact_request_received=on_contact_request_received,
                    on_contact_request_updated=noop,
                    on_contact_added=noop,
                    on_contact_removed=noop,
                )
                logger.info("Agent 2 subscribed to agent_contacts:%s", agent2_id)
                await asyncio.sleep(0.3)

                # Agent 1 sends contact request to Agent 2
                logger.info("\nAgent 1 sending contact request to Agent 2...")
                await api_client.agent_api_contacts.add_agent_contact(
                    handle=agent2_handle,
                    message="Hello from integration test!",
                )
                logger.info("Agent 1 sent contact request")

                # Wait for WebSocket event
                try:
                    await asyncio.wait_for(request_received.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Timeout waiting for contact_request_received event")

            # Verify event
            assert received_payload is not None, (
                "Should have received contact request payload"
            )
            assert received_payload.from_handle == agent1_handle, (
                f"Expected from_handle={agent1_handle}, got {received_payload.from_handle}"
            )
            assert received_payload.status == "pending", (
                f"Expected status=pending, got {received_payload.status}"
            )

            logger.info("\n" + "=" * 60)
            logger.info("SUCCESS: Agent 2 received contact_request_received event")
            logger.info("=" * 60)

        finally:
            # Cleanup: reject the request
            await cleanup_contact_state(api_client, api_client_2)

    async def test_contact_request_approved_flow(
        self, api_client, api_client_2, integration_settings
    ):
        """Test full approve flow: request -> approve -> contact_added.

        Flow:
        1. Clean up any existing contact state
        2. Agent 2 subscribes to contacts channel
        3. Agent 1 sends contact request
        4. Agent 2 approves request
        5. Agent 2 should receive contact_added event
        6. Clean up
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: Contact request approve flow")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        # Get agent identities
        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        agent1_name = response1.data.name
        logger.info("Agent 1: %s (%s)", agent1_name, agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_id = response2.data.id
        agent2_handle = response2.data.handle
        agent2_name = response2.data.name
        logger.info("Agent 2: %s (%s)", agent2_name, agent2_handle)

        # Track received events
        request_received = asyncio.Event()
        contact_added = asyncio.Event()
        request_payload: ContactRequestReceivedPayload | None = None
        added_payload: ContactAddedPayload | None = None

        async def on_contact_request_received(p: ContactRequestReceivedPayload):
            nonlocal request_payload
            logger.info("[Agent 2 WS] contact_request_received: %s", p.from_handle)
            request_payload = p
            request_received.set()

        async def on_contact_added(p: ContactAddedPayload):
            nonlocal added_payload
            logger.info("[Agent 2 WS] contact_added: %s (%s)", p.name, p.handle)
            added_payload = p
            contact_added.set()

        async def noop(p):
            pass

        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key_2,
            agent_id=agent2_id,
        )

        try:
            async with ws:
                await ws.join_agent_contacts_channel(
                    agent2_id,
                    on_contact_request_received=on_contact_request_received,
                    on_contact_request_updated=noop,
                    on_contact_added=on_contact_added,
                    on_contact_removed=noop,
                )
                await asyncio.sleep(0.3)

                # Agent 1 sends contact request
                logger.info("\nAgent 1 sending contact request...")
                await api_client.agent_api_contacts.add_agent_contact(
                    handle=agent2_handle,
                )

                # Wait for request event
                try:
                    await asyncio.wait_for(request_received.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Timeout waiting for contact_request_received")

                # Agent 2 approves the request
                logger.info("\nAgent 2 approving contact request...")
                assert request_payload is not None
                await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
                    action="approve",
                    request_id=request_payload.id,
                )

                # Wait for contact_added event
                try:
                    await asyncio.wait_for(contact_added.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Timeout waiting for contact_added event")

            # Verify
            assert added_payload is not None, (
                "Should have received contact_added payload"
            )
            assert added_payload.handle == agent1_handle, (
                f"Expected handle={agent1_handle}, got {added_payload.handle}"
            )

            logger.info("\n" + "=" * 60)
            logger.info("SUCCESS: Full approve flow completed")
            logger.info("=" * 60)

        finally:
            await cleanup_contact_state(api_client, api_client_2)

    async def test_contact_removed_event(
        self, api_client, api_client_2, integration_settings
    ):
        """Test contact_removed event when contact is deleted.

        Flow:
        1. Set up contact between agents (send + approve)
        2. Agent 2 subscribes to contacts channel
        3. Agent 2 removes Agent 1 as contact
        4. Agent 2 should receive contact_removed event
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: contact_removed WebSocket event")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        # Get agent identities
        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        agent1_name = response1.data.name
        logger.info("Agent 1: %s (%s)", agent1_name, agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_id = response2.data.id
        agent2_handle = response2.data.handle
        agent2_name = response2.data.name
        logger.info("Agent 2: %s (%s)", agent2_name, agent2_handle)

        # First establish contact (Agent 1 sends, Agent 2 approves)
        logger.info("\n--- Setting up contact ---")
        await api_client.agent_api_contacts.add_agent_contact(handle=agent2_handle)
        await asyncio.sleep(0.5)

        # Get the request ID from received requests
        response = await api_client_2.agent_api_contacts.list_agent_contact_requests()
        request_id = None
        received = getattr(response.data, "received", []) or []
        for req in received:
            if getattr(req, "from_handle", None) == agent1_handle:
                request_id = req.id
                break

        if request_id:
            await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
                action="approve",
                request_id=request_id,
            )
            logger.info("Contact established between agents")
            await asyncio.sleep(0.5)
        else:
            pytest.skip("Could not establish contact - no request found")

        # Track events
        contact_removed = asyncio.Event()
        removed_payload: ContactRemovedPayload | None = None

        async def on_contact_removed(p: ContactRemovedPayload):
            nonlocal removed_payload
            logger.info("[Agent 2 WS] contact_removed: %s", p.id)
            removed_payload = p
            contact_removed.set()

        async def noop(p):
            pass

        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key_2,
            agent_id=agent2_id,
        )

        try:
            async with ws:
                await ws.join_agent_contacts_channel(
                    agent2_id,
                    on_contact_request_received=noop,
                    on_contact_request_updated=noop,
                    on_contact_added=noop,
                    on_contact_removed=on_contact_removed,
                )
                await asyncio.sleep(0.3)

                # Agent 2 removes contact
                logger.info("\nAgent 2 removing contact with Agent 1...")
                await api_client_2.agent_api_contacts.remove_agent_contact(
                    handle=agent1_handle,
                )

                # Wait for event
                try:
                    await asyncio.wait_for(contact_removed.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Timeout waiting for contact_removed event")

            # Verify
            assert removed_payload is not None, (
                "Should have received contact_removed payload"
            )
            assert removed_payload.id is not None, "Removed payload should have id"

            logger.info("\n" + "=" * 60)
            logger.info("SUCCESS: contact_removed event received")
            logger.info("=" * 60)

        finally:
            await cleanup_contact_state(api_client, api_client_2)

    async def test_contact_request_rejected_flow(
        self, api_client, api_client_2, integration_settings
    ):
        """Test reject flow: request -> reject -> contact_request_updated.

        Flow:
        1. Clean up
        2. Agent 2 subscribes to contacts channel
        3. Agent 1 sends contact request
        4. Agent 2 rejects request
        5. Agent 2 should receive contact_request_updated with status=rejected
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: Contact request reject flow")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        # Get agent identities
        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        agent1_name = response1.data.name
        logger.info("Agent 1: %s (%s)", agent1_name, agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_id = response2.data.id
        agent2_handle = response2.data.handle
        agent2_name = response2.data.name
        logger.info("Agent 2: %s (%s)", agent2_name, agent2_handle)

        # Track events
        request_received = asyncio.Event()
        request_updated = asyncio.Event()
        request_payload: ContactRequestReceivedPayload | None = None
        updated_payload: ContactRequestUpdatedPayload | None = None

        async def on_contact_request_received(p: ContactRequestReceivedPayload):
            nonlocal request_payload
            logger.info("[Agent 2 WS] contact_request_received: %s", p.from_handle)
            request_payload = p
            request_received.set()

        async def on_contact_request_updated(p: ContactRequestUpdatedPayload):
            nonlocal updated_payload
            logger.info(
                "[Agent 2 WS] contact_request_updated: %s -> %s", p.id, p.status
            )
            updated_payload = p
            request_updated.set()

        async def noop(p):
            pass

        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key_2,
            agent_id=agent2_id,
        )

        try:
            async with ws:
                await ws.join_agent_contacts_channel(
                    agent2_id,
                    on_contact_request_received=on_contact_request_received,
                    on_contact_request_updated=on_contact_request_updated,
                    on_contact_added=noop,
                    on_contact_removed=noop,
                )
                await asyncio.sleep(0.3)

                # Agent 1 sends contact request
                logger.info("\nAgent 1 sending contact request...")
                await api_client.agent_api_contacts.add_agent_contact(
                    handle=agent2_handle,
                )

                # Wait for request event
                try:
                    await asyncio.wait_for(request_received.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Timeout waiting for contact_request_received")

                # Agent 2 rejects the request
                logger.info("\nAgent 2 rejecting contact request...")
                assert request_payload is not None
                await api_client_2.agent_api_contacts.respond_to_agent_contact_request(
                    action="reject",
                    request_id=request_payload.id,
                )

                # Wait for updated event
                try:
                    await asyncio.wait_for(request_updated.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pytest.fail("Timeout waiting for contact_request_updated event")

            # Verify
            assert updated_payload is not None
            assert updated_payload.status == "rejected", (
                f"Expected status=rejected, got {updated_payload.status}"
            )

            logger.info("\n" + "=" * 60)
            logger.info("SUCCESS: Reject flow completed with contact_request_updated")
            logger.info("=" * 60)

        finally:
            await cleanup_contact_state(api_client, api_client_2)


@requires_multi_agent
class TestContactWebSocketSubscription:
    """Basic subscription tests (no state changes)."""

    async def test_subscribe_to_contacts_channel(
        self, api_client, api_client_2, integration_settings
    ):
        """Test that agent can subscribe to contacts channel without errors."""
        response = await api_client.agent_api_identity.get_agent_me()
        agent_id = response.data.id

        async def noop(p):
            pass

        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key,
            agent_id=agent_id,
        )

        async with ws:
            await ws.join_agent_contacts_channel(
                agent_id,
                on_contact_request_received=noop,
                on_contact_request_updated=noop,
                on_contact_added=noop,
                on_contact_removed=noop,
            )
            await asyncio.sleep(0.2)
            await ws.leave_agent_contacts_channel(agent_id)

        # Success if no exception

    async def test_contacts_channel_with_rooms_channel(
        self, api_client, api_client_2, integration_settings
    ):
        """Test subscribing to both contacts and rooms channels simultaneously."""
        response = await api_client.agent_api_identity.get_agent_me()
        agent_id = response.data.id

        async def noop(p):
            pass

        ws = WebSocketClient(
            ws_url=integration_settings.band_ws_url,
            api_key=integration_settings.band_api_key,
            agent_id=agent_id,
        )

        async with ws:
            # Subscribe to both
            await ws.join_agent_rooms_channel(
                agent_id,
                on_room_added=noop,
                on_room_removed=noop,
            )
            await ws.join_agent_contacts_channel(
                agent_id,
                on_contact_request_received=noop,
                on_contact_request_updated=noop,
                on_contact_added=noop,
                on_contact_removed=noop,
            )
            await asyncio.sleep(0.2)

            # Leave both
            await ws.leave_agent_contacts_channel(agent_id)
            await ws.leave_agent_rooms_channel(agent_id)

        # Success if no exception
