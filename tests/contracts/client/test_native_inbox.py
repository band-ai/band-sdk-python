"""Read native receipt pages without inventing the standalone journal extension."""

from __future__ import annotations

import pytest
from pydantic import JsonValue

from band.contracts.client import ContractClient
from band.contracts.errors import ContractProtocolError
from band.contracts.tools import BoundContractTools, ContractInboxInput
from band.contracts.wire import JsonObject
from tests.contracts.client.kit import RoomPeer
from tests.contracts.client.platform import PlatformRoomPeer


class TestNativeInbox:
    @pytest.mark.parametrize("has_event", [False, True], ids=["filtered", "receipt"])
    async def test_client_and_tool_preserve_native_pages_without_a_head(
        self, has_event: bool
    ) -> None:
        peer = PlatformRoomPeer()
        if has_event:
            peer.inbox["events"] = [
                {"id": "accepted", "status": "accepted", "recipients": [peer.actor]}
            ]
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room(peer.room, peer.session, actor=peer.actor)
            page = await room.inbox(after=3, limit=5)
            assert page.cursor == 7
            assert page.events == peer.inbox["events"]
            assert page.head is None
            tools = BoundContractTools(room)
            assert await tools.inbox(ContractInboxInput(after=3, limit=5)) == peer.inbox
        assert peer.routes == [
            ("GET", peer.path + "/v1/identity"),
            ("POST", peer.path + "/v1/inbox"),
            ("POST", peer.path + "/v1/inbox"),
        ]

    @pytest.mark.parametrize(
        ("field", "value"),
        [("cursor", 2), ("cursor", "7"), ("events", {}), ("head", [])],
        ids=["cursor-regression", "cursor-type", "events-type", "head-type"],
    )
    async def test_malformed_pages_still_fail_closed(
        self, field: str, value: JsonValue
    ) -> None:
        peer = PlatformRoomPeer()
        peer.inbox[field] = value
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.room(peer.room, peer.session, actor=peer.actor)
            with pytest.raises(ContractProtocolError):
                await room.inbox(after=3, limit=5)

    async def test_gateway_still_requires_and_preserves_its_journal_head(self) -> None:
        peer = RoomPeer()
        expected_head: JsonObject = {"sequence": 2, "hash": "native"}
        async with (
            peer.client() as http,
            ContractClient(peer.connection(), http=http) as client,
        ):
            room = await client.gateway(session=peer.session, actor=peer.actor)
            tools = BoundContractTools(room)
            assert (await tools.inbox(ContractInboxInput(after=0)))[
                "head"
            ] == expected_head
            peer.inbox_head = None
            with pytest.raises(ContractProtocolError):
                await room.inbox(after=0)
