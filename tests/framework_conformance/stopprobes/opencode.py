"""Backend-stop probes for the opencode adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio
from typing import Any

import looptime

from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig
from band.client.streaming import ControlMode
from band.core.protocols import FAILURE_CODE_TIMEOUT
from band.integrations.opencode.types import OpencodeSessionState
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    BandMCPCalls,
    FakeOpencodeClient,
    event_message_updated,
    event_permission,
    event_session_idle,
    event_text_part,
    make_platform_message,
    tools_protocol,
)
from tests.adapters.waiting import spin
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    STARTUP_TIMEOUT_SECONDS,
    TURN_TIMEOUT_SECONDS,
    StopCase,
    cancel_turn,
    end_turn,
    stop_room,
    wait_until,
    waiting_on_stop,
)
from tests.framework_conformance.turnprobes import ROOM_ID
from tests.mcpbackends import backends_created_by

# The fake server numbers its sessions; a stopped seeding prompt leaves its
# session unseeded, so the room's next message runs in the second one.
SESSION_ID = "sess-1"
PROMPT_FIRST_SESSION = f"prompt {SESSION_ID}"
PROMPT_SECOND_SESSION = "prompt sess-2"
ABORT_FIRST_SESSION = f"abort {SESSION_ID}"
CLIENT_CLOSED = "client closed"
TIMEOUT_REPORTED = "timeout reported"
# What the server streams once an aborted turn winds down; a watcher still
# running would post it as the turn's fallback reply.
LATE_TEXT = "text from the stopped turn"
LATE_CALL = "call from the stopped turn"


class JournalingOpencodeClient(FakeOpencodeClient):
    """Records session prompts, aborts and its own close, in the order they happen."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.journal: list[str] = []

    async def prompt_async(self, session_id: str, **kwargs: Any) -> None:
        self.journal.append(f"prompt {session_id}")
        await super().prompt_async(session_id, **kwargs)

    async def abort_session(self, session_id: str) -> None:
        await super().abort_session(session_id)
        self.journal.append(f"abort {session_id}")

    async def close(self) -> None:
        self.journal.append(CLIENT_CLOSED)
        await super().close()


class HoldingAbortClient(JournalingOpencodeClient):
    """An abort that waits until released, and records only one that finished."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.completed: list[str] = []

    async def abort_session(self, session_id: str) -> None:
        self.entered.set()
        await self.release.wait()
        self.completed.append(session_id)
        await super().abort_session(session_id)


async def started(
    client: JournalingOpencodeClient, config: OpencodeAdapterConfig | None = None
) -> OpencodeAdapter:
    adapter = OpencodeAdapter(
        config=config or OpencodeAdapterConfig(), client_factory=lambda _: client
    )
    await adapter.on_started("OpenCode Agent", "A coding agent")
    return adapter


def deliver(adapter: OpencodeAdapter, tools: FakeAgentTools) -> asyncio.Task[None]:
    return asyncio.create_task(
        deliver_message(
            adapter,
            make_platform_message(room_id=ROOM_ID, content="long task"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=ROOM_ID,
        )
    )


async def stopped_turn_winds_down(client: JournalingOpencodeClient) -> None:
    """The server streams the stopped turn's last text and goes idle."""
    for event in (
        event_message_updated(SESSION_ID, "msg-late"),
        event_text_part(SESSION_ID, "msg-late", LATE_TEXT),
        event_session_idle(SESSION_ID),
    ):
        await client.push_event(event)
    consumed = asyncio.Event()

    async def all_consumed() -> None:
        consumed.set()

    # The event loop reaches this step only after handling every event above.
    await client.push_event(all_consumed)
    async with asyncio.timeout(STARTUP_TIMEOUT_SECONDS):
        await consumed.wait()
    # A watcher that was not cancelled resumes on the idle event and needs
    # more loop turns to relay the late text; the turn exposes no state to
    # wait on once it is stopped.
    await spin()


def posted(tools: FakeAgentTools, before: int) -> list[str]:
    return [f"posted {message['content']}" for message in tools.messages_sent[before:]]


async def end_running_turn(mode: ControlMode) -> list[str]:
    client = JournalingOpencodeClient(prompt_event_sequences=[[]])
    adapter = await started(client)
    tools = FakeAgentTools()
    turn = deliver(adapter, tools)
    await wait_until(lambda: bool(client.prompt_calls))
    await end_turn(adapter, turn, ROOM_ID, mode)
    await stopped_turn_winds_down(client)
    recorded = client.journal + posted(tools, 0)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


async def stop_detached_after_approval() -> list[str]:
    """A manual ask releases on_message; a later STOP still stops the turn."""
    client = JournalingOpencodeClient(
        prompt_event_sequences=[[event_permission(SESSION_ID, "perm-1")]]
    )
    adapter = await started(client, OpencodeAdapterConfig(approval_mode="manual"))
    tools = FakeAgentTools()
    await deliver(adapter, tools)
    asked = len(tools.messages_sent)
    await stop_room(adapter, ROOM_ID)
    await stopped_turn_winds_down(client)
    recorded = client.journal + posted(tools, asked)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


async def stop_holding_abort(
    adapter: OpencodeAdapter, client: HoldingAbortClient, tools: FakeAgentTools
) -> None:
    """Stop the running turn and leave its session's abort in flight."""
    turn = deliver(adapter, tools)
    await wait_until(lambda: bool(client.prompt_calls))
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
    await wait_until(client.entered.is_set)


async def wind_down(adapter: OpencodeAdapter, turn: asyncio.Task[None]) -> None:
    await cancel_turn(turn)
    await adapter.backend_turns.drain()
    await adapter.on_cleanup(ROOM_ID)


async def next_message_waits_for_the_abort() -> list[str]:
    """The next message prompts the session only after its abort has finished."""
    client = HoldingAbortClient(prompt_event_sequences=[[], []])
    adapter = await started(client)
    await stop_holding_abort(adapter, client, FakeAgentTools())
    next_turn = deliver(adapter, FakeAgentTools())
    await wait_until(lambda: waiting_on_stop(next_turn) or len(client.prompt_calls) > 1)
    client.release.set()
    await wait_until(lambda: len(client.prompt_calls) == 2)
    recorded = list(client.journal)
    await wind_down(adapter, next_turn)
    return recorded


async def late_mcp_call_during_held_abort() -> list[str]:
    """A tool call from the stopped turn that lands while its abort is pending
    posts on that turn's tools, not on the replacement turn's."""
    with backends_created_by() as starts:
        client = HoldingAbortClient(prompt_event_sequences=[[], []])
        adapter = await started(client)
        stopped, replacement = FakeAgentTools(), FakeAgentTools()
        await stop_holding_abort(adapter, client, stopped)
        next_turn = deliver(adapter, replacement)
        await wait_until(
            lambda: waiting_on_stop(next_turn) or len(client.prompt_calls) > 1
        )
        await BandMCPCalls(starts).band_tool_call(
            BandTool.SEND_MESSAGE,
            {"content": LATE_CALL, "mentions": ["@alice"]},
            room_id=ROOM_ID,
        )()
        landed = [
            *(
                f"stopped turn's tools got {m['content']}"
                for m in stopped.messages_sent
            ),
            *(
                f"replacement turn's tools got {m['content']}"
                for m in replacement.messages_sent
            ),
        ]
        client.release.set()
        await wait_until(lambda: len(client.prompt_calls) == 2)
        await wind_down(adapter, next_turn)
        return landed


async def cleanup_waits_for_the_abort() -> list[str]:
    """Teardown right after STOP waits for the session abort before it closes
    the client."""
    client = HoldingAbortClient(prompt_event_sequences=[[]])
    adapter = await started(client)
    await stop_holding_abort(adapter, client, FakeAgentTools())
    cleanup = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
    await wait_until(lambda: waiting_on_stop(cleanup) or cleanup.done())
    client.release.set()
    await cleanup
    return client.journal


def reported_timeout(tools: FakeAgentTools) -> list[str]:
    return [
        TIMEOUT_REPORTED
        for failure in reported_failures(tools)
        if failure["code"] == FAILURE_CODE_TIMEOUT
    ]


@looptime.enabled(strict=True)
async def timeout_reports() -> list[str]:
    """A timed-out turn aborts the session and still posts the timeout."""
    client = JournalingOpencodeClient(prompt_event_sequences=[[]])
    adapter = await started(
        client, OpencodeAdapterConfig(turn_timeout_s=TURN_TIMEOUT_SECONDS)
    )
    tools = FakeAgentTools()
    await deliver(adapter, tools)
    recorded = client.journal + reported_timeout(tools)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


@looptime.enabled(strict=True)
async def stop_during_timeout_abort(mode: ControlMode) -> list[str]:
    """A STOP while the timeout abort is in flight still finishes that abort."""
    client = HoldingAbortClient(prompt_event_sequences=[[]])
    adapter = await started(
        client, OpencodeAdapterConfig(turn_timeout_s=TURN_TIMEOUT_SECONDS)
    )
    turn = deliver(adapter, FakeAgentTools())
    await client.entered.wait()
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, mode)
    client.release.set()
    await adapter.backend_turns.drain()
    recorded = list(client.completed)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


async def approve(adapter: OpencodeAdapter) -> None:
    """The human approves the pending ask, as a room message the control hook takes."""
    message = make_platform_message(room_id=ROOM_ID, content="approve req-1")
    assert await adapter.try_handle_control(message, tools_protocol(FakeAgentTools()))


@looptime.enabled(strict=True)
async def stop_released_turn_during_timeout_abort(mode: ControlMode) -> list[str]:
    """A released turn that timed out has already ended on its own: a STOP during
    its timeout abort only finishes the abort, and the timeout is still reported."""
    client = HoldingAbortClient(
        prompt_event_sequences=[[event_permission(SESSION_ID, "req-1")]]
    )
    adapter = await started(
        client,
        OpencodeAdapterConfig(
            approval_mode="manual", turn_timeout_s=TURN_TIMEOUT_SECONDS
        ),
    )
    tools = FakeAgentTools()
    await deliver(adapter, tools)
    await approve(adapter)
    await client.entered.wait()
    await adapter.on_interrupt(ROOM_ID, mode)
    client.release.set()
    await adapter.backend_turns.drain()
    await wait_until(lambda: bool(reported_timeout(tools)))
    recorded = reported_timeout(tools)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


async def idle() -> list[str]:
    client = JournalingOpencodeClient()
    adapter = await started(client)
    await stop_room(adapter, ROOM_ID)
    recorded = list(client.journal)
    await adapter.on_cleanup(ROOM_ID)
    return recorded


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=[PROMPT_FIRST_SESSION, ABORT_FIRST_SESSION],
        )
        for mode in ENDING_CONTROLS
    },
    "stop-detached-after-approval": StopCase(
        run=stop_detached_after_approval,
        expected=[PROMPT_FIRST_SESSION, ABORT_FIRST_SESSION],
    ),
    "next-message-waits-for-the-abort": StopCase(
        run=next_message_waits_for_the_abort,
        expected=[PROMPT_FIRST_SESSION, ABORT_FIRST_SESSION, PROMPT_SECOND_SESSION],
    ),
    "late-mcp-call-during-held-abort": StopCase(
        run=late_mcp_call_during_held_abort,
        expected=[f"stopped turn's tools got {LATE_CALL}"],
    ),
    "cleanup-waits-for-the-abort": StopCase(
        run=cleanup_waits_for_the_abort,
        expected=[PROMPT_FIRST_SESSION, ABORT_FIRST_SESSION, CLIENT_CLOSED],
    ),
    "timeout": StopCase(
        run=timeout_reports,
        expected=[PROMPT_FIRST_SESSION, ABORT_FIRST_SESSION, TIMEOUT_REPORTED],
    ),
    **{
        f"{mode}-during-timeout-abort": StopCase(
            run=lambda mode=mode: stop_during_timeout_abort(mode),
            expected=[SESSION_ID],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-released-turn-during-timeout-abort": StopCase(
            run=lambda mode=mode: stop_released_turn_during_timeout_abort(mode),
            expected=[TIMEOUT_REPORTED],
        )
        for mode in ENDING_CONTROLS
    },
    "idle-room": StopCase(run=idle, expected=[]),
}
