"""The baseline invocation observer sees every turn an adapter runs and parks only the one asked for.

It drives a real batch-capable adapter through its real singleton and batch entry
points: what the observer reports must be what the adapter's own turn builder
received, and a parked turn must not have reached that builder yet.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.e2e.baseline.toolkit.invocations import InvocationObserver
from tests.framework_conformance.invocationkit import (
    ROOM_ID,
    DecliningAdapter,
    original,
    turn_input,
)

DEADLINE_S = 5.0


@pytest.fixture
def watched() -> tuple[DecliningAdapter, InvocationObserver]:
    adapter, observer = DecliningAdapter(), InvocationObserver(deadline_s=DEADLINE_S)
    observer.watch(adapter)
    return adapter, observer


class TestInvocationObserver:
    async def test_singleton_and_batch_turns_are_reported_with_their_originals(
        self, watched: tuple[DecliningAdapter, InvocationObserver]
    ) -> None:
        adapter, observer = watched

        await adapter.on_event(turn_input(original("m1")))
        await adapter.on_messages(turn_input(original("m2"), original("m3")))

        assert observer.partition(ROOM_ID) == [("m1",), ("m2", "m3")]
        assert adapter.built == [("m1",), ("m2", "m3")]

    async def test_turns_are_reported_per_room(
        self, watched: tuple[DecliningAdapter, InvocationObserver]
    ) -> None:
        adapter, observer = watched

        await adapter.on_messages(turn_input(original("m1"), room_id="room-a"))
        await adapter.on_messages(turn_input(original("m2"), room_id="room-b"))

        assert observer.partition("room-a") == [("m1",)]
        assert observer.partition("room-b") == [("m2",)]

    async def test_a_held_turn_waits_for_release_before_reaching_the_adapter(
        self, watched: tuple[DecliningAdapter, InvocationObserver]
    ) -> None:
        adapter, observer = watched
        hold = observer.hold_next(ROOM_ID)

        turn = asyncio.create_task(adapter.on_messages(turn_input(original("m1"))))
        parked = await hold.entered()
        built_while_parked = list(adapter.built)
        hold.release()
        await turn

        assert parked.message_ids == ("m1",)
        assert built_while_parked == []
        assert adapter.built == [("m1",)]

    async def test_a_hold_parks_only_the_next_turn_of_its_room(
        self, watched: tuple[DecliningAdapter, InvocationObserver]
    ) -> None:
        adapter, observer = watched
        hold = observer.hold_next("room-a")

        await adapter.on_messages(turn_input(original("m1"), room_id="room-b"))
        parked = asyncio.create_task(
            adapter.on_messages(turn_input(original("m2"), room_id="room-a"))
        )
        await hold.entered()
        await adapter.on_messages(turn_input(original("m3"), room_id="room-a"))
        hold.release()
        await parked

        assert adapter.built == [("m1",), ("m3",), ("m2",)]

    async def test_releasing_all_frees_a_parked_turn_and_parks_no_later_one(
        self, watched: tuple[DecliningAdapter, InvocationObserver]
    ) -> None:
        adapter, observer = watched
        parked_hold = observer.hold_next(ROOM_ID)
        observer.hold_next(ROOM_ID)

        turn = asyncio.create_task(adapter.on_messages(turn_input(original("m1"))))
        await parked_hold.entered()
        observer.release_all()
        await turn
        await adapter.on_messages(turn_input(original("m2")))

        assert adapter.built == [("m1",), ("m2",)]
