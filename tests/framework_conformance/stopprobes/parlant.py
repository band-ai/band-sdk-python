"""Backend-stop probes for the Parlant adapter on the real in-process engine;
see ``stopprobes``.

The engine answers a session from its own background task, so the evidence is
what Parlant itself records: the session's status events after the stop, and
whether that task is the one ``process_session_tag`` names. Every case skips
outside the dev-parlant venv.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

from band.adapters.parlant import ParlantAdapter, ParlantAdapterConfig
from band.adapters.parlant.messages import process_session_tag
from band.client.streaming import ControlMode
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_room,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

LOOPBACK = "127.0.0.1"
# Real engine work (startup aside) settles well within this, and it stays
# under the suite's 30s test timeout so a turn that never stops fails here.
ENGINE_WAIT_SECONDS = 15.0
STATUS_POLL_SECONDS = 0.05
CANCELLED_STATUS = "cancelled"
READY_STATUS = "ready"
# Parlant's preamble task records its own statuses alongside the stopped
# turn's, so only the statuses a stop decides are compared.
STOP_STATUSES = (CANCELLED_STATUS, READY_STATUS)
TAG_IS_LIVE = "process_session_tag names the engine's running task"


@asynccontextmanager
async def started() -> AsyncIterator[tuple[ParlantAdapter, Any]]:
    """A started adapter on an owned server whose model never answers."""
    import parlant.sdk as p  # noqa: PLC0415 -- dev-parlant extra

    from tests.integrations.parlant.stubnlp import (  # noqa: PLC0415 -- dev-parlant extra
        StubNLP,
    )

    nlp = StubNLP()
    adapter = ParlantAdapter(
        ParlantAdapterConfig(),
        nlp_service=nlp.factory,
        server_options={"host": LOOPBACK, "log_level": p.LogLevel.WARNING},
    )
    await adapter.on_started("Agent", "A Parlant agent")
    try:
        yield adapter, nlp
    finally:
        await adapter.cleanup_all()


def background_tasks(adapter: ParlantAdapter) -> Any:
    from parlant.core.background_tasks import (  # noqa: PLC0415 -- dev-parlant extra
        BackgroundTaskService,
    )

    return adapter.server.container[BackgroundTaskService]


async def processing_turn(
    adapter: ParlantAdapter, nlp: Any, tools: FakeAgentTools
) -> tuple[asyncio.Task[None], str]:
    """A turn the engine is generating for, and its session id."""
    turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
    await asyncio.wait_for(nlp.entered.wait(), ENGINE_WAIT_SECONDS)
    assert adapter._rooms is not None
    session_id = await adapter._rooms.session_for(ROOM_ID, customer_name="")
    return turn, str(session_id)


async def statuses(adapter: ParlantAdapter, session_id: str) -> list[str]:
    from parlant.core.sessions import EventKind  # noqa: PLC0415 -- dev-parlant extra

    assert adapter._rooms is not None
    events: Sequence[Any] = await adapter._rooms.app.sessions.find_events(
        session_id=session_id,
        min_offset=0,
        kinds=[EventKind.STATUS],
        source=None,
        trace_id=None,
    )
    return [event.data.get("status") for event in events]


async def stop_statuses_after(
    adapter: ParlantAdapter, session_id: str, seen: int
) -> list[str]:
    """The stopped turn's wind-down Parlant records after the first ``seen``:
    from its cancellation to the session being ready again."""

    async def until_settled() -> list[str]:
        while True:
            new = [
                status
                for status in (await statuses(adapter, session_id))[seen:]
                if status in STOP_STATUSES
            ]
            if CANCELLED_STATUS in new and new[-1] == READY_STATUS:
                return new[new.index(CANCELLED_STATUS) :]
            await asyncio.sleep(STATUS_POLL_SECONDS)

    return await asyncio.wait_for(until_settled(), ENGINE_WAIT_SECONDS)


async def end_running_turn(mode: ControlMode) -> list[str]:
    async with started() as (adapter, nlp):
        tools = FakeAgentTools()
        turn, session_id = await processing_turn(adapter, nlp, tools)
        seen = len(await statuses(adapter, session_id))
        await end_turn(adapter, turn, ROOM_ID, mode)
        after = await stop_statuses_after(adapter, session_id, seen)
        return [*after, *(f"posted {sent}" for sent in tools.messages_sent)]


async def tag_parity() -> list[str]:
    async with started() as (adapter, nlp):
        turn, session_id = await processing_turn(adapter, nlp, FakeAgentTools())
        live = process_session_tag(session_id) in background_tasks(adapter)._tasks
        await end_turn(adapter, turn, ROOM_ID, ControlMode.STOP)
        return [TAG_IS_LIVE] if live else []


def record_cancels(adapter: ParlantAdapter) -> list[str]:
    """Record every tag the engine is asked to cancel, still cancelling it."""
    service = background_tasks(adapter)
    cancel = service.cancel
    cancelled: list[str] = []

    async def recording(*, tag: str, reason: str = "(not given)") -> None:
        cancelled.append(tag)
        await cancel(tag=tag, reason=reason)

    service.cancel = recording
    return cancelled


async def idle() -> list[str]:
    async with started() as (adapter, _nlp):
        cancelled = record_cancels(adapter)
        await stop_room(adapter, ROOM_ID)
        return cancelled


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=list(STOP_STATUSES),
        )
        for mode in ENDING_CONTROLS
    },
    "tag-matches-the-engine": StopCase(run=tag_parity, expected=[TAG_IS_LIVE]),
    "idle-room": StopCase(run=idle, expected=[]),
}
