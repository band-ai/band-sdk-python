"""Live contact events reach a CALLBACK handler as typed payloads.

Offline tests hand-write contact payloads; this proves the real platform's
``agent_contacts:<id>`` pushes hydrate into the SDK's typed payloads and reach
``ContactEventConfig(strategy=CALLBACK)``, and that approving through the
``ContactTools`` the callback receives completes the relationship. The
requester is a second provisioned agent because the platform refuses a user's
request to their own agent. Both agents are reaped on teardown, and deleting an
agent deletes its contacts, so no relationship outlives the run.

No adapter runs, so the test is pinned to one lane.
"""

from __future__ import annotations

import pytest

from band.client.streaming import (
    ContactAddedPayload,
    ContactRequestReceivedPayload,
    ContactRequestUpdatedPayload,
)
from band.platform.event import (
    ContactAddedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
)
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.settings import BaselineSettings
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.contacts import ContactInbox, running_contact_listener
from tests.e2e.baseline.toolkit.provisioning import ResourceManager

PENDING = "pending"
APPROVED = "approved"
APPROVE = "approve"
AGENT_CONTACT_TYPE = "Agent"


@lane(Lane.CORE)  # a platform/runtime contract, not a framework's; prove it once
class TestContactEvents:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_request_and_approval_arrive_as_typed_contact_events(
        self, resource_manager: ResourceManager, baseline_settings: BaselineSettings
    ) -> None:
        deadline_s = baseline_settings.e2e_timeout
        target = await resource_manager.provision_agent("contacttarget")
        requester = await resource_manager.provision_agent("contactrequester")
        target_actor = resource_manager.peer(target)
        requester_actor = resource_manager.peer(requester)
        requester_handle = await requester_actor.handle()
        note = unique_marker("contact")
        inbox = ContactInbox()

        async with running_contact_listener(target, inbox, baseline_settings):
            request_id = await requester_actor.request_contact(
                await target_actor.handle(), message=note
            )
            received = await inbox.wait_for(
                ContactRequestReceivedEvent, deadline_s=deadline_s
            )
            assert isinstance(received.payload, ContactRequestReceivedPayload)
            assert (
                received.payload.id,
                received.payload.from_handle,
                received.payload.from_name,
                received.payload.message,
                received.payload.status,
            ) == (request_id, requester_handle, requester.name, note, PENDING)

            await inbox.tools.respond_contact_request(APPROVE, request_id=request_id)
            updated = await inbox.wait_for(
                ContactRequestUpdatedEvent, deadline_s=deadline_s
            )
            added = await inbox.wait_for(ContactAddedEvent, deadline_s=deadline_s)

        assert isinstance(updated.payload, ContactRequestUpdatedPayload)
        assert (updated.payload.id, updated.payload.status) == (request_id, APPROVED)
        assert isinstance(added.payload, ContactAddedPayload)
        assert (
            added.payload.handle,
            added.payload.name,
            added.payload.type,
        ) == (requester_handle, requester.name, AGENT_CONTACT_TYPE)
