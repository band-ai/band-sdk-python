"""Backend-stop probes for the Parlant adapter on the real in-process engine;
see ``stopprobes``.

The engine answers a session from its own background task, and the stub model
never answers, so that task ends only when it is stopped. The evidence is that
the task ``process_session_tag`` names finishes after the stop, and that the
preamble child captured before the stop is done, with nothing posted. Every
case skips outside the dev-parlant venv.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest

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
ENGINE_STOPPED = "engine stopped processing the session"
PREAMBLE_STOPPED = "preamble generation stopped"
TAG_IS_LIVE = "process_session_tag names the engine's running task"
RESPONSE_TIMEOUT_SECONDS = 5.0
# Binary ticks keep Parlant's exact-zero expiry check free of float residuals.
VIRTUAL_CLOCK_RESOLUTION_SECONDS = 2**-20


@asynccontextmanager
async def started(
    config: ParlantAdapterConfig | None = None,
) -> AsyncIterator[tuple[ParlantAdapter, Any]]:
    """A started adapter on an owned server whose model never answers."""
    import parlant.sdk as p  # noqa: PLC0415 -- dev-parlant extra

    from tests.integrations.parlant.stubnlp import (  # noqa: PLC0415 -- dev-parlant extra
        StubNLP,
    )

    nlp = StubNLP()
    adapter = ParlantAdapter(
        config or ParlantAdapterConfig(),
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


async def end_running_turn(mode: ControlMode) -> list[str]:
    async with started() as (adapter, nlp), captured_preambles() as (created, preamble):
        tools = FakeAgentTools()
        turn, session_id = await processing_turn(adapter, nlp, tools)
        engine = background_tasks(adapter)._tasks[process_session_tag(session_id)]
        await created.wait()
        await nlp.generation_started(preamble[0]).wait()
        await end_turn(adapter, turn, ROOM_ID, mode)
        return stopped_work(engine, preamble, tools)


@asynccontextmanager
async def captured_preambles() -> AsyncIterator[
    tuple[asyncio.Event, list[asyncio.Task[Any]]]
]:
    """Observe the actual child factory independently of adapter discovery."""
    from parlant.core.engines.alpha.engine import (  # noqa: PLC0415 -- dev-parlant extra
        AlphaEngine,
    )

    create_preamble = AlphaEngine._get_preamble_task
    children: list[asyncio.Task[Any]] = []
    created = asyncio.Event()

    async def capture(engine: Any, context: Any) -> asyncio.Task[bool]:
        child = await create_preamble(engine, context)
        children.append(child)
        created.set()
        return child

    with patch.object(AlphaEngine, "_get_preamble_task", capture):
        try:
            yield created, children
        finally:
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)


def stopped_work(
    engine: asyncio.Task[Any], preamble: list[asyncio.Task[Any]], tools: FakeAgentTools
) -> list[str]:
    return [
        *([ENGINE_STOPPED] if engine.done() else []),
        *([PREAMBLE_STOPPED] if preamble and all(t.done() for t in preamble) else []),
        *(f"posted {sent}" for sent in tools.messages_sent),
    ]


async def response_timeout() -> list[str]:
    config = ParlantAdapterConfig(
        response_timeout=RESPONSE_TIMEOUT_SECONDS,
        response_poll=RESPONSE_TIMEOUT_SECONDS,
    )
    async with (
        started(config) as (adapter, nlp),
        captured_preambles() as (created, preamble),
    ):
        assert adapter._rooms is not None
        sessions = adapter._rooms.app.sessions
        post = sessions.create_customer_message
        processing: list[asyncio.Task[Any]] = []

        async def post_when_generating(**kwargs: Any) -> Any:
            event = await post(**kwargs)
            parent = background_tasks(adapter)._tasks[
                process_session_tag(kwargs["session_id"])
            ]
            processing.append(parent)
            # The response budget starts after this returns to the adapter.
            await created.wait()
            await nlp.generation_started(preamble[0]).wait()
            assert not parent.done() and not preamble[0].done()
            return event

        tools = FakeAgentTools()
        try:
            with patch.object(
                sessions, "create_customer_message", post_when_generating
            ):
                await adapter.on_event(turn_input(tools))
            [engine] = processing
            return stopped_work(engine, preamble, tools)
        finally:
            for parent in processing:
                parent.cancel()
            await asyncio.gather(*processing, return_exceptions=True)


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
            expected=[ENGINE_STOPPED, PREAMBLE_STOPPED],
        )
        for mode in ENDING_CONTROLS
    },
    "tag-matches-the-engine": StopCase(run=tag_parity, expected=[TAG_IS_LIVE]),
    "response-timeout": StopCase(
        run=response_timeout,
        expected=[ENGINE_STOPPED, PREAMBLE_STOPPED],
        marks=(pytest.mark.looptime(resolution=VIRTUAL_CLOCK_RESOLUTION_SECONDS),),
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
