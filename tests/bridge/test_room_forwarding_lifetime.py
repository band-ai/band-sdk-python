"""Room forwarding state stays owned until every accepted handler settles."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack

import pytest

from tests.bridge.kit import (
    GatedForwarder,
    Mark,
    accept_event,
    await_cancelled,
    build_runner,
    make_message_event,
    make_room_removed_event,
    retained_rooms,
)


class TestRoomForwardingLifetime:
    @pytest.mark.parametrize(
        "messages_only, expected_events",
        [
            (
                False,
                [
                    (Mark.START, "a"),
                    (Mark.END, "a"),
                    (Mark.START, "r1"),
                    (Mark.END, "r1"),
                    (Mark.START, "c"),
                    (Mark.END, "c"),
                    (Mark.START, "d"),
                    (Mark.END, "d"),
                ],
            ),
            (
                True,
                [
                    (Mark.START, "a"),
                    (Mark.END, "a"),
                    (Mark.START, "c"),
                    (Mark.END, "c"),
                    (Mark.START, "d"),
                    (Mark.END, "d"),
                ],
            ),
        ],
    )
    async def test_teardown_cannot_split_room_serialization(
        self, messages_only: bool, expected_events: list[tuple[str, str]]
    ) -> None:
        forwarder = GatedForwarder(("a", "c"))
        runner, _, _ = build_runner(forwarder=forwarder, messages_only=messages_only)
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            a = group.create_task(
                runner._safe_handle_event(make_message_event(message_id="a"))
            )
            cleanup.callback(forwarder.release["a"].set)
            cleanup.callback(forwarder.release["c"].set)
            await forwarder.started["a"].wait()
            b_entered, c_entered, d_entered = (
                asyncio.Event(),
                asyncio.Event(),
                asyncio.Event(),
            )
            b = group.create_task(
                accept_event(runner, make_room_removed_event(), b_entered)
            )
            await b_entered.wait()
            c = group.create_task(
                accept_event(runner, make_message_event(message_id="c"), c_entered)
            )
            await c_entered.wait()
            forwarder.release["a"].set()
            await b
            await forwarder.started["c"].wait()
            d = group.create_task(
                accept_event(runner, make_message_event(message_id="d"), d_entered)
            )
            await d_entered.wait()
            assert (
                forwarder.events
                == expected_events[: expected_events.index((Mark.START, "c")) + 1]
            )
            forwarder.release["c"].set()
            await asyncio.gather(a, c, d)
        assert forwarder.events == expected_events
        assert retained_rooms(runner) == set()

    @pytest.mark.parametrize("concurrency", [1, 32])
    async def test_cancelled_teardown_releases_after_last_waiter(
        self, concurrency: int
    ) -> None:
        forwarder = GatedForwarder(("a",))
        runner, _, _ = build_runner(
            forwarder=forwarder, max_concurrent_forwards=concurrency
        )
        async with asyncio.TaskGroup() as group, AsyncExitStack() as cleanup:
            cleanup.callback(forwarder.release["a"].set)
            group.create_task(
                runner._safe_handle_event(make_message_event(message_id="a"))
            )
            await forwarder.started["a"].wait()
            entered = asyncio.Event()
            b = group.create_task(
                accept_event(runner, make_room_removed_event(), entered)
            )
            await entered.wait()
            b.cancel()
            await await_cancelled(b)
            assert retained_rooms(runner) == {"r1"}
        assert retained_rooms(runner) == set()

    @pytest.mark.parametrize(
        "failure, expected_ids",
        [(None, ["m1", "followup"]), (RuntimeError("forward failed"), ["followup"])],
    )
    async def test_settled_work_releases_room_without_a_membership_event(
        self, failure: Exception | None, expected_ids: list[str]
    ) -> None:
        runner, forwarder, _ = build_runner()
        forwarder.forward_side_effect = failure
        await runner._safe_handle_event(make_message_event())
        assert retained_rooms(runner) == set()
        forwarder.forward_side_effect = None
        await runner._safe_handle_event(make_message_event("followup"))
        assert [p["payload"]["id"] for p in forwarder.forwarded] == expected_ids
        assert retained_rooms(runner) == set()
