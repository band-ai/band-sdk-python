"""Reconciliation acknowledges released reservations only in the owning session."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest
from band_sdk_core import chat_room_topic, room_participants_topic

from band.client.streaming import WebSocketClient
from band.platform.subscriptions import SubscriptionManager
from tests.platform.conftest import cancelled_mid_await
from tests.toolkit.tasks import background


class TestReservationReconciliation:
    @pytest.mark.parametrize(
        ("retained", "leave_error", "subscribed"),
        [
            (frozenset(), RuntimeError("leave already ended"), True),
            (frozenset(), None, True),
            (frozenset({chat_room_topic("room")}), None, False),
            (frozenset({room_participants_topic("room")}), None, False),
        ],
    )
    async def test_room_release_is_decided_by_membership_not_leave_return(
        self,
        retained: frozenset[str],
        leave_error: Exception | None,
        subscribed: bool,
        room_event_handlers: dict[str, Callable[[Any], Awaitable[None]]],
    ) -> None:
        transport = create_autospec(WebSocketClient, instance=True)
        manager = SubscriptionManager(lambda: transport)
        handlers = room_event_handlers
        async with cancelled_mid_await(
            transport.join_chat_room_channel,
            manager.subscribe_room(transport, "room", **handlers),
        ):
            pass
        transport.joined_topics.return_value = retained
        transport.leave_chat_room_channel.side_effect = leave_error
        await manager.drain_reconciliation(transport)
        await manager.subscribe_room(transport, "room", **handlers)
        assert manager.is_room_subscribed("room") is subscribed

    async def test_replaced_session_keeps_its_own_room_quarantine(
        self, room_event_handlers: dict[str, Callable[[Any], Awaitable[None]]]
    ) -> None:
        original = create_autospec(WebSocketClient, instance=True)
        replacement = create_autospec(WebSocketClient, instance=True)
        current = SimpleNamespace(ws=original)
        manager = SubscriptionManager(lambda: current.ws)
        handlers = room_event_handlers
        async with cancelled_mid_await(
            original.join_chat_room_channel,
            manager.subscribe_room(original, "room", **handlers),
        ):
            pass
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held_leave(room_id: str) -> None:
            entered.set()
            await release.wait()

        original.leave_chat_room_channel.side_effect = held_leave
        original.joined_topics.return_value = frozenset()
        async with background(manager.drain_reconciliation(original)) as draining:
            await entered.wait()
            manager.end_session()
            current.ws = replacement
            async with cancelled_mid_await(
                replacement.join_chat_room_channel,
                manager.subscribe_room(replacement, "room", **handlers),
            ):
                pass
            release.set()
            await draining
        await manager.subscribe_room(replacement, "room", **handlers)
        assert not manager.is_room_subscribed("room")
        replacement.joined_topics.return_value = frozenset()
        await manager.drain_reconciliation(replacement)
        await manager.subscribe_room(replacement, "room", **handlers)
        assert manager.is_room_subscribed("room")

    async def test_retained_room_does_not_block_other_room_reconciliation(
        self, room_event_handlers: dict[str, Callable[[Any], Awaitable[None]]]
    ) -> None:
        transport = create_autospec(WebSocketClient, instance=True)
        manager = SubscriptionManager(lambda: transport)
        for room_id in ("retained", "released"):
            async with cancelled_mid_await(
                transport.join_chat_room_channel,
                manager.subscribe_room(transport, room_id, **room_event_handlers),
            ):
                pass
        transport.joined_topics.return_value = frozenset({chat_room_topic("retained")})
        await manager.drain_reconciliation(transport)
        await manager.subscribe_room(transport, "retained", **room_event_handlers)
        await manager.subscribe_room(transport, "released", **room_event_handlers)
        assert (
            manager.is_room_subscribed("retained"),
            manager.is_room_subscribed("released"),
        ) == (False, True)
