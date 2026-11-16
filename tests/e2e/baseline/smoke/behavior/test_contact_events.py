"""Live contact events reach a CALLBACK handler as typed payloads.

Offline tests hand-write contact payloads; this proves the real platform's
``agent_contacts:<id>`` pushes hydrate into the SDK's typed payloads and reach
``ContactEventConfig(strategy=CALLBACK)``, and that responding through the
``ContactTools`` the callback receives settles the request: an approval adds
the contact (which removing then deletes), a rejection adds nothing.

No adapter runs, so the test is pinned to one lane.
"""

from __future__ import annotations

from unittest.mock import ANY

import pytest

from band.client.streaming.wire import WirePayload
from band.core.types import ContactRequestAction, ContactRequestStatus
from band.platform.event import (
    ContactAddedEvent,
    ContactRemovedEvent,
    ContactRequestReceivedEvent,
    ContactRequestUpdatedEvent,
)
from tests.e2e.baseline.agents import Lane, lane
from tests.e2e.baseline.smoke.samples.sample_agents import unique_marker
from tests.e2e.baseline.toolkit.contacts import ContactParties, running_contact_parties
from tests.e2e.baseline.toolkit.provisioning import ResourceManager

AGENT_CONTACT_TYPE = "Agent"


def typed_fields(payload: WirePayload | None) -> dict[str, object]:
    """The payload's declared fields; wire extras (e.g. avatar URLs) are not typed."""
    assert payload is not None
    return payload.model_dump(include=set(type(payload).model_fields))


async def received_request(parties: ContactParties) -> str:
    """Send a contact request to the target; assert its typed push; return its id."""
    note = unique_marker("contact")
    request_id = await parties.requester_actor.request_contact(
        parties.target_handle, message=note
    )
    received = await parties.inbox.wait_for(ContactRequestReceivedEvent)
    assert typed_fields(received.payload) == {
        "id": request_id,
        "from_handle": parties.requester_handle,
        "from_name": parties.requester.name,
        "message": note,
        "status": ContactRequestStatus.PENDING,
        "inserted_at": ANY,
    }
    return request_id


@lane(Lane.CORE)  # a platform/runtime contract, not a framework's; prove it once
class TestContactEvents:
    @pytest.mark.asyncio(loop_scope="session")
    async def test_approval_adds_and_removal_deletes_the_contact(
        self, resource_manager: ResourceManager
    ) -> None:
        async with running_contact_parties(resource_manager) as parties:
            request_id = await received_request(parties)
            await parties.inbox.tools.respond_contact_request(
                ContactRequestAction.APPROVE, request_id=request_id
            )
            updated = await parties.inbox.wait_for(ContactRequestUpdatedEvent)
            added = await parties.inbox.wait_for(ContactAddedEvent)
            listed_after_add = await parties.inbox.tools.list_contacts()
            await parties.inbox.tools.remove_contact(handle=parties.requester_handle)
            removed = await parties.inbox.wait_for(ContactRemovedEvent)
            listed_after_remove = await parties.inbox.tools.list_contacts()

        assert typed_fields(updated.payload) == {
            "id": request_id,
            "status": ContactRequestStatus.APPROVED,
        }
        assert typed_fields(added.payload) == {
            "id": ANY,
            "handle": parties.requester_handle,
            "name": parties.requester.name,
            "type": AGENT_CONTACT_TYPE,
            "description": parties.requester.description,
            # What the platform reports for a provisioned agent; is_external
            # is is_remote's legacy alias.
            "is_remote": True,
            "is_external": True,
            "inserted_at": ANY,
        }
        contact_id = typed_fields(added.payload)["id"]
        assert listed_after_add["contacts"] == [
            {
                "id": contact_id,
                "handle": parties.requester_handle,
                "name": parties.requester.name,
                "type": AGENT_CONTACT_TYPE,
            }
        ]
        assert typed_fields(removed.payload) == {"id": contact_id}
        assert listed_after_remove["contacts"] == []

    @pytest.mark.asyncio(loop_scope="session")
    async def test_rejection_settles_the_request_without_a_contact(
        self, resource_manager: ResourceManager
    ) -> None:
        async with running_contact_parties(resource_manager) as parties:
            request_id = await received_request(parties)
            await parties.inbox.tools.respond_contact_request(
                ContactRequestAction.REJECT, request_id=request_id
            )
            updated = await parties.inbox.wait_for(ContactRequestUpdatedEvent)
            contacts = await parties.inbox.tools.list_contacts()

        assert typed_fields(updated.payload) == {
            "id": request_id,
            "status": ContactRequestStatus.REJECTED,
        }
        assert contacts["contacts"] == []
