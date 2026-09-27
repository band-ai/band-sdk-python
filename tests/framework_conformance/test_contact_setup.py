"""Shared approval identities keep their contact relationship across CI jobs."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest
from band_rest.core.api_error import ApiError

from tests.e2e.baseline.toolkit.user_ops import UserOps


@dataclass
class ContactState:
    contacts: set[frozenset[str]] = field(default_factory=set)
    pending: dict[str, tuple[str, str]] = field(default_factory=dict)
    approvals: list[str] = field(default_factory=list)
    create_auto_accepts_inverse: bool = False

    def connected(self, first: str, second: str) -> bool:
        return frozenset({first, second}) in self.contacts

    def accept(self, requester: str, recipient: str) -> None:
        self.contacts.add(frozenset({requester, recipient}))


class ContactAPI:
    def __init__(self, state: ContactState, owner: str) -> None:
        self.state = state
        self.owner = owner

    async def list_my_contacts(self, **_kwargs: Any) -> SimpleNamespace:
        others = [
            next(iter(pair - {self.owner}))
            for pair in self.state.contacts
            if self.owner in pair
        ]
        return SimpleNamespace(
            data=[SimpleNamespace(contact_id=other) for other in others],
            metadata=SimpleNamespace(total_pages=1),
        )

    async def list_received_contact_requests(self, **_kwargs: Any) -> SimpleNamespace:
        received = [
            SimpleNamespace(id=request_id, requester_id=requester)
            for request_id, (requester, recipient) in self.state.pending.items()
            if recipient == self.owner
        ]
        return SimpleNamespace(data=received, metadata=SimpleNamespace(total_pages=1))

    async def create_contact_request(self, *, contact_request: Any) -> SimpleNamespace:
        recipient = contact_request.recipient_handle
        if self.state.create_auto_accepts_inverse:
            self.state.pending["inverse"] = (recipient, self.owner)
        inverse = next(
            (
                request_id
                for request_id, pair in self.state.pending.items()
                if pair == (recipient, self.owner)
            ),
            None,
        )
        if inverse is not None:
            self.state.pending.pop(inverse)
            self.state.accept(self.owner, recipient)
            return SimpleNamespace(data=SimpleNamespace(id=inverse))
        if self.state.connected(self.owner, recipient):
            raise ApiError(status_code=409)
        if (self.owner, recipient) in self.state.pending.values():
            raise ApiError(status_code=409)
        self.state.pending["new"] = (self.owner, recipient)
        return SimpleNamespace(data=SimpleNamespace(id="new"))

    async def approve_contact_request(self, request_id: str) -> None:
        request = self.state.pending.pop(request_id, None)
        if request is None:
            raise ApiError(status_code=409)
        requester, recipient = request
        assert recipient == self.owner
        self.state.approvals.append(request_id)
        self.state.accept(requester, recipient)

    async def remove_my_contact(self, **_kwargs: Any) -> None:
        pytest.fail("shared contact was removed while another job may need it")


def user(state: ContactState, name: str) -> UserOps:
    async def profile() -> SimpleNamespace:
        return SimpleNamespace(data=SimpleNamespace(id=name, handle=name))

    client = SimpleNamespace(
        human_api_contacts=ContactAPI(state, name),
        human_api_profile=SimpleNamespace(get_my_profile=profile),
    )
    return UserOps(client)


@pytest.mark.parametrize(
    "pending", ["existing", "outgoing", "incoming", "inverse_race", "none"]
)
async def test_contact_setup_reuses_both_pending_directions_and_preserves_access(
    pending: str,
) -> None:
    state = ContactState()
    if pending == "existing":
        state.accept("owner", "second")
    elif pending == "outgoing":
        state.pending["outgoing"] = ("owner", "second")
    elif pending == "incoming":
        state.pending["incoming"] = ("second", "owner")
    elif pending == "inverse_race":
        state.create_auto_accepts_inverse = True

    owner, second = user(state, "owner"), user(state, "second")
    async with owner.contact_with(second) as second_id:
        assert second_id == "second"
        assert state.connected("owner", "second")
    async with owner.contact_with(second):
        assert state.connected("owner", "second")
    assert state.connected("owner", "second")
    assert state.approvals == (
        [pending]
        if pending in {"outgoing", "incoming"}
        else ["new"]
        if pending == "none"
        else []
    )


async def test_concurrent_contact_setup_keeps_shared_relationship() -> None:
    state = ContactState()
    owner, second = user(state, "owner"), user(state, "second")

    async def setup() -> None:
        async with owner.contact_with(second):
            assert state.connected("owner", "second")

    await asyncio.gather(setup(), setup())
    assert state.connected("owner", "second")
