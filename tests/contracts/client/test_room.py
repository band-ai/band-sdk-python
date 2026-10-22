"""Native authority identity, retries and receipt ownership."""

from __future__ import annotations

import pytest

from band.contracts.client import ContractClient
from band.contracts.errors import (
    ContractIdentityError,
    ContractInboxConflict,
    ContractTransportError,
)
from band.contracts.wire import JsonObject
from tests.contracts.client.kit import RoomPeer, hydrated_rules


class TestRoom:
    async def test_lost_ack_retries_the_original_snapshot_and_exposes_native_receipts(
        self,
    ) -> None:
        peer = RoomPeer()
        async with peer.client() as http:
            async with ContractClient(peer.connection(), http=http) as client:
                room = await client.room("room", "session", actor=peer.actor)
                payload: JsonObject = {"text": "original"}
                proposal = room.proposal(
                    id="stable", kind="question", payload=payload, expected_version=1
                )
                payload["text"] = "changed"
                peer.lose_next_ack = True
                with pytest.raises(ContractTransportError):
                    await room.propose(proposal)
                accepted = await room.propose(proposal)
                page = await room.inbox(after=0)
                assert accepted["status"] == "duplicate"
                original = accepted["original"]
                assert isinstance(original, dict)
                assert original["status"] == "accepted"
                assert peer.proposals[0] == peer.proposals[1]
                assert b"original" in peer.proposals[1]
                assert page.cursor == 2
                assert page.head["hash"] == "native"
                assert page.events[0]["id"] == "acceptance"
            assert not http.is_closed

    async def test_identity_mismatch_blocks_room_operations(self) -> None:
        peer = RoomPeer()
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            with pytest.raises(ContractIdentityError):
                await client.room("room", "session", actor="Agent:other")
        assert len(peer.requests) == 1

    async def test_compact_definition_is_bound_and_cached_without_mutable_aliasing(
        self,
    ) -> None:
        peer = RoomPeer()
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room("room", "session", actor=peer.actor)
            first = await room.view(hydrate=True)
            hydrated_rules(first).append("caller edit")
            second = await room.view(hydrate=True)
            assert hydrated_rules(second) == ["pinned"]
            assert (
                sum(
                    request.url.path.endswith("/definition")
                    for request in peer.requests
                )
                == 1
            )
            with pytest.raises(ContractInboxConflict):
                await room.inbox(after=3)

    async def test_definition_from_another_actor_is_refused(self) -> None:
        peer = RoomPeer()
        peer.definition_actor = "Agent:other"
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room("room", "session", actor=peer.actor)
            with pytest.raises(ContractIdentityError):
                await room.definition()
