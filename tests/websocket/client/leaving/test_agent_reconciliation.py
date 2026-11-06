"""Agent-topic reconciliation restores delivery only in its owning session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import pytest
from phoenix_channels_python_client import ChannelError

from band.client.streaming import ContactAddedPayload, WebSocketClient
from band.platform.subscriptions import SubscriptionManager
from tests.identifiers import AGENT_ID
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.tasks import background
from tests.toolkit.timecontrol import controlled_clock
from tests.websocket.client.leaving.contacts import (
    CONTACT_TOPIC,
    send_contact_event,
)
from tests.websocket.client.leaving.kit import LeavePeer

CONTACT = ContactAddedPayload(
    id="contact-123",
    handle="jane_smith",
    name="Jane Smith",
    type="User",
    inserted_at="2026-02-09T10:35:00Z",
)


class TestAgentReconciliation:
    @pytest.mark.parametrize("accepted", [True, False])
    async def test_released_cleanup_restores_contact_delivery(
        self,
        ws: WebSocketClient,
        leave_peer: LeavePeer,
        contact_handlers: dict[str, Callable[..., Awaitable[None]]],
        contact_events: asyncio.Queue[ContactAddedPayload],
        accepted: bool,
    ) -> None:
        manager = SubscriptionManager(lambda: ws)
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        assert ws.client is not None
        subscription = ws.client.topic_subscriptions[CONTACT_TOPIC]
        async with background(
            manager.unsubscribe_agent_contacts(ws, AGENT_ID)
        ) as leaving:
            connection, message = await leave_peer.leaves.get()
            leaving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaving
        await leave_peer.reply(connection, message, accepted)
        assert subscription.leave_task is not None
        await asyncio.gather(subscription.leave_task, return_exceptions=True)
        await leave_peer.end_channel(connection, message)
        await subscription.terminal_received.wait()
        await manager.drain_reconciliation(ws)
        leave_peer.released.set()
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        await send_contact_event(connection, CONTACT)
        assert await contact_events.get() == CONTACT

    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_retained_cleanup_blocks_fresh_contact_membership(
        self,
        ws: WebSocketClient,
        leave_peer: LeavePeer,
        contact_handlers: dict[str, Callable[..., Awaitable[None]]],
        contact_events: asyncio.Queue[ContactAddedPayload],
    ) -> None:
        manager = SubscriptionManager(lambda: ws)
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        leave_peer.accepted = False
        leave_peer.released.set()
        await manager.unsubscribe_agent_contacts(ws, AGENT_ID)
        assert ws.client is not None
        subscription = ws.client.topic_subscriptions[CONTACT_TOPIC]
        entered = asyncio.Event()

        async def drain() -> None:
            entered.set()
            await manager.drain_reconciliation(ws)

        async with controlled_clock() as clock, background(drain()) as draining:
            await entered.wait()
            await clock.elapse(ws.client.leave_timeout_s)
            await draining
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        with pytest.raises(ChannelError, match="already has a live channel"):
            await ws.join_agent_contacts_channel(AGENT_ID, **contact_handlers)
        assert ws.joined_topics() == frozenset({CONTACT_TOPIC})
        connection, message = await leave_peer.leaves.get()
        await leave_peer.end_channel(connection, message)
        await subscription.terminal_received.wait()
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        assert ws.joined_topics() == frozenset()
        await manager.drain_reconciliation(ws)
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        await send_contact_event(connection, CONTACT)
        assert await contact_events.get() == CONTACT

    async def test_old_cleanup_cannot_clear_replacement_contact_quarantine(
        self,
        ws: WebSocketClient,
        leave_peer: LeavePeer,
        contact_handlers: dict[str, Callable[..., Awaitable[None]]],
        contact_events: asyncio.Queue[ContactAddedPayload],
    ) -> None:
        current = SimpleNamespace(ws=ws)
        manager = SubscriptionManager(lambda: current.ws)
        await manager.subscribe_agent_contacts(ws, AGENT_ID, **contact_handlers)
        assert ws.client is not None
        original_subscription = ws.client.topic_subscriptions[CONTACT_TOPIC]
        async with background(
            manager.unsubscribe_agent_contacts(ws, AGENT_ID)
        ) as leaving:
            original_connection, original_message = await leave_peer.leaves.get()
            leaving.cancel()
            with pytest.raises(asyncio.CancelledError):
                await leaving
        entered = asyncio.Event()

        async def drain_original() -> None:
            entered.set()
            await manager.drain_reconciliation(ws)

        async with background(drain_original()) as draining:
            await entered.wait()
            async with WebSocketClient(leave_peer.url, "test-key") as replacement:
                manager.end_session()
                current.ws = replacement
                await manager.subscribe_agent_contacts(
                    replacement, AGENT_ID, **contact_handlers
                )
                assert replacement.client is not None
                replacement_subscription = replacement.client.topic_subscriptions[
                    CONTACT_TOPIC
                ]
                async with background(
                    manager.unsubscribe_agent_contacts(replacement, AGENT_ID)
                ) as leaving_replacement:
                    (
                        replacement_connection,
                        replacement_message,
                    ) = await leave_peer.leaves.get()
                    leaving_replacement.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await leaving_replacement
                await leave_peer.reply(original_connection, original_message, True)
                await leave_peer.end_channel(original_connection, original_message)
                await original_subscription.terminal_received.wait()
                await draining
                await leave_peer.reply(
                    replacement_connection, replacement_message, True
                )
                await leave_peer.end_channel(
                    replacement_connection, replacement_message
                )
                assert replacement_subscription.leave_task is not None
                await replacement_subscription.leave_task
                await replacement_subscription.terminal_received.wait()
                leave_peer.released.set()
                await manager.subscribe_agent_contacts(
                    replacement, AGENT_ID, **contact_handlers
                )
                assert replacement.joined_topics() == frozenset()
                await manager.drain_reconciliation(replacement)
                await manager.subscribe_agent_contacts(
                    replacement, AGENT_ID, **contact_handlers
                )
                await send_contact_event(replacement_connection, CONTACT)
                assert await contact_events.get() == CONTACT
