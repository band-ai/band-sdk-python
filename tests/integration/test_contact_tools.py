"""Integration tests for ContactTools.

These tests verify ContactTools operations against a real API:
- list_contacts
- add_contact / remove_contact
- list_contact_requests
- respond_contact_request (approve, reject)

Setup:
- Agent 1: Primary test agent (BAND_API_KEY)
- Agent 2: Secondary test agent (BAND_API_KEY_2)

Run with: uv run pytest tests/integration/test_contact_tools.py -v -s
"""

import asyncio
import logging

from band.runtime.contact_tools import ContactTools
from tests.integration.conftest import requires_api, requires_multi_agent

logger = logging.getLogger(__name__)


async def cleanup_contact_state(api_client, api_client_2):
    """Clean up any existing contact state between the two agents."""
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
        pass

    # Agent 2: Remove contact with Agent 1 if exists
    try:
        await api_client_2.agent_api_contacts.remove_agent_contact(handle=agent1_handle)
        logger.info("  Agent 2 removed contact with Agent 1")
    except Exception:
        pass

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

    await asyncio.sleep(0.3)
    logger.info("Cleanup complete")


@requires_api
class TestContactToolsListContacts:
    """Test list_contacts against real API."""

    async def test_list_contacts_real_api(self, api_client):
        """List contacts against real API."""
        logger.info("\n" + "=" * 60)
        logger.info("Testing: ContactTools.list_contacts")
        logger.info("=" * 60)

        tools = ContactTools(api_client)
        result = await tools.list_contacts()

        # Verify structure
        assert "contacts" in result
        assert "metadata" in result
        assert isinstance(result["contacts"], list)
        assert "page" in result["metadata"]
        assert "total_count" in result["metadata"]

        logger.info("Contacts count: %d", len(result["contacts"]))
        logger.info("Total count: %d", result["metadata"]["total_count"])

        logger.info("SUCCESS: list_contacts works correctly")


@requires_multi_agent
class TestContactToolsAddRemoveFlow:
    """Test add_contact and remove_contact flow."""

    async def test_add_and_remove_contact_flow(self, api_client, api_client_2):
        """Full add → approve → remove cycle using ContactTools.

        Flow:
        1. Clean up any existing contact state
        2. Agent 1 sends contact request to Agent 2 (via ContactTools)
        3. Agent 2 approves (via REST, simulating different handling)
        4. Agent 1 removes contact (via ContactTools)
        5. Verify contact is removed
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: ContactTools add and remove contact flow")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        # Get agent handles
        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        agent1_name = response1.data.name
        logger.info("Agent 1: %s (%s)", agent1_name, agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle
        agent2_name = response2.data.name
        logger.info("Agent 2: %s (%s)", agent2_name, agent2_handle)

        tools1 = ContactTools(api_client)
        tools2 = ContactTools(api_client_2)

        try:
            # 1. Agent 1 sends contact request
            logger.info("\n--- Agent 1 sending contact request ---")
            result = await tools1.add_contact(
                agent2_handle, message="Integration test request"
            )
            assert result["status"] in ("pending", "approved")
            logger.info("Request sent, status: %s", result["status"])
            await asyncio.sleep(0.5)

            # 2. Agent 2 approves (using ContactTools)
            if result["status"] == "pending":
                logger.info("\n--- Agent 2 approving request ---")
                requests = await tools2.list_contact_requests()
                logger.info("Agent 2 received requests: %d", len(requests["received"]))

                # Find the request from Agent 1
                request_id = None
                for req in requests["received"]:
                    if req["from_handle"] == agent1_handle:
                        request_id = req["id"]
                        break

                assert request_id is not None, (
                    f"Agent 2 should have received request from {agent1_handle}"
                )

                approve_result = await tools2.respond_contact_request(
                    "approve", request_id=request_id
                )
                assert approve_result["status"] == "approved"
                logger.info("Request approved")
                await asyncio.sleep(0.5)

            # 3. Verify contact exists
            logger.info("\n--- Verifying contact exists ---")
            contacts = await tools1.list_contacts()
            contact_handles = [c["handle"] for c in contacts["contacts"]]
            assert agent2_handle in contact_handles, (
                f"Agent 2 ({agent2_handle}) should be in Agent 1's contacts"
            )
            logger.info("Contact verified in list")

            # 4. Agent 1 removes contact
            logger.info("\n--- Agent 1 removing contact ---")
            remove_result = await tools1.remove_contact(handle=agent2_handle)
            assert remove_result["status"] == "removed"
            logger.info("Contact removed")
            await asyncio.sleep(0.5)

            # 5. Verify contact is gone
            logger.info("\n--- Verifying contact removed ---")
            contacts_after = await tools1.list_contacts()
            contact_handles_after = [c["handle"] for c in contacts_after["contacts"]]
            assert agent2_handle not in contact_handles_after, (
                "Agent 2 should no longer be in Agent 1's contacts"
            )

            logger.info("\n" + "=" * 60)
            logger.info("SUCCESS: Add and remove contact flow completed")
            logger.info("=" * 60)

        finally:
            await cleanup_contact_state(api_client, api_client_2)


@requires_multi_agent
class TestContactRequestFlows:
    """Test contact request approve/reject flows."""

    async def test_contact_request_approve_flow(self, api_client, api_client_2):
        """Send request → approve → verify contact.

        Tests using ContactTools for all operations.
        """
        logger.info("\n" + "=" * 60)
        logger.info("Testing: Contact request approve flow")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        logger.info("Agent 1: %s", agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle
        logger.info("Agent 2: %s", agent2_handle)

        tools1 = ContactTools(api_client)
        tools2 = ContactTools(api_client_2)

        try:
            # Send request
            logger.info("\n--- Sending contact request ---")
            add_result = await tools1.add_contact(agent2_handle)
            if add_result["status"] == "approved":
                logger.info("Request auto-approved (inverse request existed)")
            else:
                assert add_result["status"] == "pending"
                await asyncio.sleep(0.5)

                # Approve request
                logger.info("\n--- Approving request ---")
                requests = await tools2.list_contact_requests()
                request_id = None
                for req in requests["received"]:
                    if req["from_handle"] == agent1_handle:
                        request_id = req["id"]
                        break

                assert request_id is not None
                result = await tools2.respond_contact_request(
                    "approve", request_id=request_id
                )
                assert result["status"] == "approved"

            # Verify contact
            await asyncio.sleep(0.5)
            contacts = await tools2.list_contacts()
            contact_handles = [c["handle"] for c in contacts["contacts"]]
            assert agent1_handle in contact_handles

            logger.info("\nSUCCESS: Approve flow completed")

        finally:
            await cleanup_contact_state(api_client, api_client_2)

    async def test_contact_request_reject_flow(self, api_client, api_client_2):
        """Send request → reject → verify no contact."""
        logger.info("\n" + "=" * 60)
        logger.info("Testing: Contact request reject flow")
        logger.info("=" * 60)

        await cleanup_contact_state(api_client, api_client_2)

        response1 = await api_client.agent_api_identity.get_agent_me()
        agent1_handle = response1.data.handle
        logger.info("Agent 1: %s", agent1_handle)

        response2 = await api_client_2.agent_api_identity.get_agent_me()
        agent2_handle = response2.data.handle
        logger.info("Agent 2: %s", agent2_handle)

        tools1 = ContactTools(api_client)
        tools2 = ContactTools(api_client_2)

        try:
            # Send request
            logger.info("\n--- Sending contact request ---")
            add_result = await tools1.add_contact(agent2_handle)
            if add_result["status"] == "approved":
                # Clean and retry
                await cleanup_contact_state(api_client, api_client_2)
                add_result = await tools1.add_contact(agent2_handle)

            assert add_result["status"] == "pending"
            await asyncio.sleep(0.5)

            # Reject request
            logger.info("\n--- Rejecting request ---")
            requests = await tools2.list_contact_requests()
            request_id = None
            for req in requests["received"]:
                if req["from_handle"] == agent1_handle:
                    request_id = req["id"]
                    break

            assert request_id is not None, "Should have pending request from Agent 1"
            result = await tools2.respond_contact_request(
                "reject", request_id=request_id
            )
            assert result["status"] == "rejected"

            # Verify no contact was created
            await asyncio.sleep(0.5)
            contacts = await tools2.list_contacts()
            contact_handles = [c["handle"] for c in contacts["contacts"]]
            assert agent1_handle not in contact_handles, (
                "Agent 1 should NOT be in contacts after rejection"
            )

            logger.info("\nSUCCESS: Reject flow completed, no contact created")

        finally:
            await cleanup_contact_state(api_client, api_client_2)
