"""HTTP observations at discovery, context hydration and delivery admission."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from band.client.streaming import DeliveryStatus, MessageMetadata
from band.platform.event import MessageEvent
from band.runtime.execution import ExecutionContext
from band.runtime.sessionconfig import SessionConfig
from tests.platform.settlement.kit import link_over
from tests.runtime.delivery.kit import RestPeer, delivery_entry
from tests.runtime.helpers import (
    AGENT_ID,
    ROOM_ID,
    STOP_SECONDS,
    WAIT_SECONDS,
    rest_client_over,
)


@dataclass(frozen=True)
class HandoffObservation:
    effects: tuple[str, ...]
    original_preserved: bool
    target_writes: tuple[str, ...]
    blocked_claimed: bool
    blocked_status: DeliveryStatus | None


async def observe_handoff(
    *,
    control: bool,
    batching: bool,
    warm: bool,
    history: DeliveryStatus,
    stale: DeliveryStatus,
    discovery: DeliveryStatus = DeliveryStatus.PROCESSING,
) -> HandoffObservation:
    peer = RestPeer()
    event = peer.post("target", status=discovery)
    assert event.payload is not None
    event.payload.metadata = MessageMetadata(
        status="sent", delivery_status={AGENT_ID: delivery_entry(stale)}
    )
    original = event.payload.model_dump()
    effects: list[str] = []
    delivered: list[dict[str, object]] = []

    async def answer(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/context"):
            row = peer.rows["target"].wire
            row["metadata"]["delivery_status"] = {AGENT_ID: delivery_entry(history)}
            return httpx.Response(
                200,
                json={"data": [row], "metadata": {"has_more": False, "limit": 50}},
            )
        return peer.answer(request)

    def record(kind: str, event: MessageEvent) -> None:
        assert event.payload is not None
        effects.append(kind)
        delivered.append(event.payload.model_dump())

    async def execute(ctx: ExecutionContext, event: MessageEvent) -> None:
        record("ordinary", event)

    async def batch(ctx: ExecutionContext, events: tuple[MessageEvent, ...]) -> None:
        await execute(ctx, events[0])

    async def handle(ctx: ExecutionContext, event: MessageEvent) -> bool:
        record("control", event)
        return True

    def recognizes(ctx: ExecutionContext, event: MessageEvent) -> bool:
        return control

    async with rest_client_over(answer) as rest:
        ctx = ExecutionContext(
            ROOM_ID,
            link_over(rest),
            execute,
            config=SessionConfig(
                enable_context_hydration=True,
                enable_working_state=False,
                idle_resync_seconds=60,
            ),
            agent_id=AGENT_ID,
            on_execute_batch=batch,
            on_message_control=handle if control else None,
            is_message_control=recognizes if control else None,
        )
        if warm:
            await ctx.hydrate()
        if control:
            peer.post("blocked", at=-1)
            peer.refuse_processing.add("blocked")
        await ctx.on_event(event)
        try:
            async with asyncio.timeout(WAIT_SECONDS):
                await ctx.scheduler.run_pass(recovery=True)
        finally:
            await ctx.stop(timeout=STOP_SECONDS)
    return HandoffObservation(
        effects=tuple(effects),
        original_preserved=event.payload.model_dump() == original
        and all(payload == original for payload in delivered),
        target_writes=tuple(
            entry for entry in peer.without_reads() if entry.endswith(" target")
        ),
        blocked_claimed=bool(peer.writes_of(DeliveryStatus.PROCESSING, "blocked")),
        blocked_status=peer.status_of("blocked") if control else None,
    )
