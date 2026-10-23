"""Leaves of present topics still require confirmed transport completion."""

from __future__ import annotations

import asyncio

import pytest

from band.platform.link import BandLink
from tests.toolkit.tasks import background
from tests.websocket.client.leaving.kit import ROOM_ID, LeavePeer


class TestLeaveOutcomes:
    async def test_present_topics_wait_for_the_server_acknowledgement(
        self, link: BandLink, leave_peer: LeavePeer
    ) -> None:
        await link.subscribe_room(ROOM_ID)
        async with background(link.unsubscribe_room(ROOM_ID)) as leaving:
            await leave_peer.requested.wait()
            assert not leaving.done()

            leave_peer.released.set()
            await leaving

        await link.subscribe_room(ROOM_ID)
        assert link.is_room_subscribed(ROOM_ID)

    async def test_rejected_leave_keeps_the_room_quarantined(
        self, link: BandLink, leave_peer: LeavePeer
    ) -> None:
        await link.subscribe_room(ROOM_ID)
        leave_peer.accepted = False
        async with background(link.unsubscribe_room(ROOM_ID)) as leaving:
            await leave_peer.requested.wait()
            leave_peer.released.set()
            await leaving

        await link.subscribe_room(ROOM_ID)
        assert not link.is_room_subscribed(ROOM_ID)

    async def test_cancelled_leave_keeps_the_room_quarantined(
        self, link: BandLink, leave_peer: LeavePeer
    ) -> None:
        await link.subscribe_room(ROOM_ID)
        async with background(link.unsubscribe_room(ROOM_ID)) as leaving:
            await leave_peer.requested.wait()
            leaving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaving

        await link.subscribe_room(ROOM_ID)
        assert not link.is_room_subscribed(ROOM_ID)
