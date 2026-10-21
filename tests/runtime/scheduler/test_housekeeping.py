"""Roster events keep progressing while model turns are paused or refused."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from band.client.streaming import DeliveryStatus
from band.core.exceptions import InputNotAccepted
from band.platform.event import MessageEvent, PlatformEvent
from band.runtime.execution import ExecutionContext
from tests.conftest import make_participant_added_event, make_participant_removed_event
from tests.platform.settlement.kit import link_over
from tests.runtime.delivery.kit import RestPeer, session_config
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    STOP_SECONDS,
    WAIT_SECONDS,
    rest_client_over,
    synthetic_message,
)


class TestHousekeeping:
    @pytest.mark.parametrize("batching", [False])
    @pytest.mark.parametrize(
        ("stopped", "head_status"),
        [
            pytest.param(False, DeliveryStatus.PROCESSING, id="refused"),
            pytest.param(True, None, id="stopped"),
        ],
    )
    async def test_roster_changes_pass_paused_inputs_without_running_them(
        self, batching: bool, stopped: bool, head_status: DeliveryStatus | None
    ) -> None:
        peer = RestPeer()
        ordinary = peer.post("head")
        attempted = asyncio.Event()
        done = asyncio.Event()
        roster: list[tuple[str, list[str]]] = []

        def answer(request: httpx.Request) -> httpx.Response:
            if stopped and request.url.path.endswith("/next"):
                return httpx.Response(204)
            return peer.answer(request)

        async def execute(ctx: ExecutionContext, event: PlatformEvent) -> None:
            if isinstance(event, MessageEvent):
                attempted.set()
                raise InputNotAccepted("busy")
            roster.append((event.type, [person["id"] for person in ctx.participants]))
            if event.type == "participant_removed":
                done.set()

        async def batch(
            ctx: ExecutionContext, events: tuple[MessageEvent, ...]
        ) -> None:
            await execute(ctx, events[0])

        async with rest_client_over(answer) as rest:
            ctx = ExecutionContext(
                ROOM_ID,
                link_over(rest),
                execute,
                config=session_config(batching=batching),
                agent_id=AGENT_ID,
                on_execute_batch=batch,
            )
            await ctx.hydrate()
            ctx.add_participant({"id": "old", "name": "Old", "type": "User"})
            if stopped:
                ctx.stop_room()
            await ctx.on_event(ordinary)
            await ctx.start()
            try:
                async with asyncio.timeout(WAIT_SECONDS):
                    if not stopped:
                        await attempted.wait()
                    await ctx.on_event(synthetic_message("local"))
                    await ctx.on_event(
                        make_participant_added_event(
                            room_id=ROOM_ID,
                            participant_id="new",
                            name="New",
                            type="User",
                        )
                    )
                    await ctx.on_event(
                        make_participant_removed_event(
                            room_id=ROOM_ID, participant_id="old"
                        )
                    )
                    await done.wait()
                assert ctx.is_stopped is stopped
            finally:
                await ctx.stop(timeout=STOP_SECONDS)
        assert roster == [
            ("participant_added", ["old", "new"]),
            ("participant_removed", ["new"]),
        ]
        assert peer.status_of("head") is head_status
        assert not any(write.message_id == "local" for write in peer.writes)
