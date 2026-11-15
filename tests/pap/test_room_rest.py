from __future__ import annotations

from typing import Any

import pytest
from bandpap.room import RestRoomSender, RoomDelivery
from bandpap.storage import StateConflict, StateUnavailable

from band.client.rest import aclose_rest_client
from tests.pap.restkit import BandHTTP


class TestRoomRest:
    @pytest.mark.asyncio
    async def test_actual_generated_client_posts_structured_mentions_with_no_retry(
        self,
    ) -> None:
        wire = BandHTTP()
        client = wire.client()
        sender = RestRoomSender({"target": client}, authorize=wire.authorize)
        try:
            result = await sender.send(
                RoomDelivery(
                    id="d",
                    room="room",
                    proxy="target",
                    recipients=("muse",),
                    grant="grant",
                    text="hello",
                )
            )
            assert result == "remote"
            assert wire.posts() == [
                {
                    "message": {
                        "content": "hello",
                        "mentions": [{"id": "muse", "kind": "mention"}],
                    }
                }
            ]
        finally:
            await aclose_rest_client(client)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("condition", ["wrong_identity", "revoked_during_identity"])
    async def test_identity_or_authority_change_prevents_message_post(
        self, condition: Any
    ) -> None:
        wire = BandHTTP()
        wire.proxy = "bestbuy" if condition == "wrong_identity" else "target"
        wire.revoke_on_identity = condition == "revoked_during_identity"
        client = wire.client()
        sender = RestRoomSender({"target": client}, authorize=wire.authorize)
        try:
            with pytest.raises((StateUnavailable, PermissionError)):
                await sender.send(
                    RoomDelivery(
                        id="d",
                        room="room",
                        proxy="target",
                        recipients=("muse",),
                        grant="grant",
                        text="hello",
                    )
                )
            assert wire.posts() == []
        finally:
            await aclose_rest_client(client)

    @pytest.mark.asyncio
    async def test_acknowledgment_for_wrong_recipient_is_not_success(self) -> None:
        wire = BandHTTP()
        wire.recipient = "bestbuy"
        client = wire.client()
        sender = RestRoomSender({"target": client}, authorize=wire.authorize)
        try:
            with pytest.raises(StateConflict):
                await sender.send(
                    RoomDelivery(
                        id="d",
                        room="room",
                        proxy="target",
                        recipients=("muse",),
                        grant="grant",
                        text="hello",
                    )
                )
        finally:
            await aclose_rest_client(client)
