"""Channel reservations release only after terminal confirmation and cleanup."""

from __future__ import annotations

import asyncio

import pytest
from phoenix_channels_python_client import ChannelError, ProtocolVersion
from phoenix_channels_python_client.codec import encode_message
from phoenix_channels_python_client.utils import make_message

from band.client.streaming import WebSocketClient
from band.client.streaming.client import WireEvent
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.kit import VALID_MESSAGE_CREATED_PAYLOAD
from tests.websocket.client.leaving.kit import ROOM_ID, ROOM_TOPIC, LeavePeer


class TestChannelCleanup:
    async def test_leave_reply_waits_for_the_terminal_close(
        self, ws: WebSocketClient, leave_peer: LeavePeer
    ) -> None:
        received = asyncio.Queue()
        await ws.join_chat_room_channel(ROOM_ID, received.put)
        leave_peer.close_released.clear()
        assert ws.client is not None
        subscription = ws.client.topic_subscriptions[ROOM_TOPIC]
        async with background(ws.leave_chat_room_channel(ROOM_ID)) as leaving:
            await leave_peer.requested.wait()
            leave_peer.released.set()
            await leave_peer.close_requested.wait()
            assert subscription.leave_task is not None
            await subscription.leave_task
            # A completed dependency leave proves the reply was processed.
            with pytest.raises(TimeoutError):
                async with asyncio.timeout(0):
                    await ws.leave_chat_room_channel(ROOM_ID)
            assert not leaving.done()
            assert ROOM_TOPIC in ws.joined_topics()
            leave_peer.close_released.set()
            await leaving
        await ws.join_chat_room_channel(ROOM_ID, received.put)
        assert ws.joined_topics() == frozenset({ROOM_TOPIC})

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_repeated_rejected_leave_cannot_claim_a_retained_reservation(
        self, ws: WebSocketClient, leave_peer: LeavePeer
    ) -> None:
        received = asyncio.Queue()
        await ws.join_chat_room_channel(ROOM_ID, received.put)
        leave_peer.accepted = False
        leave_peer.released.set()
        with pytest.raises(ChannelError, match="Leave acknowledgement rejected"):
            await ws.leave_chat_room_channel(ROOM_ID)
        entered = asyncio.Event()

        async def repeat_leave() -> None:
            entered.set()
            await ws.leave_chat_room_channel(ROOM_ID)

        async with controlled_clock() as clock, background(repeat_leave()) as leaving:
            await entered.wait()
            # The clock controls the public leave deadline, not peer readiness.
            assert ws.client is not None
            await clock.elapse(ws.client.leave_timeout_s)
            with pytest.raises(TimeoutError):
                await leaving
        with pytest.raises(ChannelError, match="already has a live channel"):
            await ws.join_chat_room_channel(ROOM_ID, received.put)
        assert ws.joined_topics() == frozenset({ROOM_TOPIC})

    async def test_cancelled_leave_keeps_cleanup_owned_until_terminal_confirmation(
        self, ws: WebSocketClient, leave_peer: LeavePeer
    ) -> None:
        received = asyncio.Queue()
        await ws.join_chat_room_channel(ROOM_ID, received.put)
        async with background(ws.leave_chat_room_channel(ROOM_ID)) as leaving:
            connection, message = await leave_peer.leaves.get()
            leaving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaving
        with pytest.raises(ChannelError, match="already has a live channel"):
            await ws.join_chat_room_channel(ROOM_ID, received.put)
        leave_peer.close_released.clear()
        leave_peer.released.set()
        await leave_peer.close_requested.wait()
        async with background(ws.leave_chat_room_channel(ROOM_ID)) as confirming:
            leave_peer.close_released.set()
            await confirming
        await ws.join_chat_room_channel(ROOM_ID, received.put)
        await leave_peer.reply(connection, message, True)
        # An old leave acknowledgment cannot terminate the replacement membership.
        await connection.send(
            encode_message(
                make_message(
                    topic=ROOM_TOPIC,
                    event=WireEvent.MESSAGE_CREATED,
                    payload=VALID_MESSAGE_CREATED_PAYLOAD,
                ),
                ProtocolVersion.V2,
            )
        )
        assert (await received.get()).id == VALID_MESSAGE_CREATED_PAYLOAD["id"]
