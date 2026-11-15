"""Backend-stop probes for the letta adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import band_sdk_core
import looptime
import pytest

from band.adapters.letta import LettaAdapterConfig
from band.client.streaming import ControlMode
from band.core.exceptions import ProviderUnavailableError
from band.core.protocols import TurnResultAlreadyReported
from band.core.retention import cleanup
from band.runtime.tools import CHAT_ID_FIELD_NAME, BandTool
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    TURN_TIMEOUT_SECONDS,
    StopCase,
    cancel_turn,
    end_turn,
    stop_room,
    wait_until,
    waiting_on_stop,
)
from tests.framework_conformance.turnprobes import ALICE, ROOM_ID, turn_input

AGENT_ID = "agent-1"
CONVERSATION_ID = "conversation-1"
CANCEL_AGENT_RUNS = f"cancel agent {AGENT_ID}"
CANCEL_CONVERSATION_RUNS = f"cancel conversation {CONVERSATION_ID}"
SEND_AGENT = f"send agent {AGENT_ID}"
DELETE_AGENT = f"delete agent {AGENT_ID}"
FAILURE_POSTED = "failure posted"
NEXT_TURN_REFUSED = "next turn refused as unavailable"
LATE_CALL_LANDED = "late call from the stopped run reached a room"
CANCEL_FAILED_WARNING = "cancel failure logged at WARNING"
SHUTDOWN_RETURNED = "shutdown returned"


class JournaledTools(FakeAgentTools):
    """Room tools that log each failure post into the Letta client's journal."""

    def __init__(self, journal: list[str]) -> None:
        super().__init__(participants=[ALICE])
        self.journal = journal

    async def send_failure(self, failure: band_sdk_core.AgentFailure) -> Any:
        self.journal.append(FAILURE_POSTED)
        return await super().send_failure(failure)


class WarningRecords(logging.Handler):
    """Collects the WARNING records of the logger it is attached to."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def held_adapter(
    *,
    shared: bool = False,
    timeout_s: float | None = None,
    delete_agents: bool = False,
) -> Any:
    from tests.adapters.lettakit import (  # noqa: PLC0415 -- letta extra, absent from some lane venvs
        RecordingLettaClient,
        ready_letta_adapter,
    )

    settings: dict[str, Any] = {"mode": "shared"} if shared else {}
    if timeout_s is not None:
        settings["turn_timeout_s"] = timeout_s
    if delete_agents:
        settings["delete_agents_on_cleanup"] = True
    adapter, _ = ready_letta_adapter(LettaAdapterConfig(**settings), room_id=ROOM_ID)
    client = RecordingLettaClient()
    client.hold()
    adapter._client = client
    room = adapter._rooms[ROOM_ID]
    room.agent_id = AGENT_ID
    if shared:
        room.conversation_id = CONVERSATION_ID
    return adapter, client


async def end_running_turn(mode: ControlMode, *, shared: bool = False) -> list[str]:
    adapter, client = held_adapter(shared=shared)
    turn = asyncio.create_task(
        adapter.on_event(turn_input(FakeAgentTools(participants=[ALICE])))
    )
    await wait_until(lambda: bool(client.sent))
    await end_turn(adapter, turn, ROOM_ID, mode)
    return client.cancels


async def server_rejects_cancel() -> list[str]:
    """Without Redis the cancel fails and is logged, but its answer proves
    nothing: the room stays unavailable and its next turn is never sent."""
    adapter, client = held_adapter()
    client.reject_cancels = True
    turn = asyncio.create_task(
        adapter.on_event(turn_input(FakeAgentTools(participants=[ALICE])))
    )
    await wait_until(lambda: bool(client.sent))
    warnings = WarningRecords()
    cleanup.logger.addHandler(warnings)
    try:
        await end_turn(adapter, turn, ROOM_ID, ControlMode.STOP)
    finally:
        cleanup.logger.removeHandler(warnings)
    stop_failures = [
        record
        for record in warnings.records
        if f"backend stop for room {ROOM_ID}" in record.getMessage()
    ]
    client.release.set()
    refused = await next_turn_is_refused(adapter)
    return [
        *client.sent,
        *client.cancels,
        *([CANCEL_FAILED_WARNING] if stop_failures else []),
        *([NEXT_TURN_REFUSED] if refused else []),
        *(f"replayed: {entry}" for entry in client.sent[1:]),
    ]


async def next_turn_is_refused(adapter: Any) -> bool:
    """Whether the room's next turn fails as unavailable, posting nothing."""
    tools = FakeAgentTools(participants=[ALICE])
    try:
        await adapter.on_event(turn_input(tools))
    except ProviderUnavailableError:
        return not tools.messages_sent
    return False


async def stop_holding_cancel(
    adapter: Any, client: Any, tools: FakeAgentTools
) -> asyncio.Event:
    """Stop the running turn and leave its run cancel in flight; the returned
    event lets that cancel finish."""
    release_cancel = client.hold_cancel()
    turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
    await wait_until(lambda: bool(client.sent))
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
    await wait_until(client.cancel_started.is_set)
    return release_cancel


def effects(client: Any) -> list[str]:
    """What the server was asked to do besides take the turn."""
    return [entry for entry in client.journal if entry not in client.sent]


async def late_mcp_call_during_held_cancel() -> list[str]:
    """The stopped run's origin is revoked while its cancel is pending: a late
    tool call from it reaches no room, and the replacement turn is refused."""
    from tests.adapters.lettakit import (  # noqa: PLC0415 -- letta extra, absent from some lane venvs
        band_mcp_registrations,
    )

    adapter, client = held_adapter()
    stopped = FakeAgentTools(participants=[ALICE])
    release_cancel = await stop_holding_cancel(adapter, client, stopped)
    refused = await next_turn_is_refused(adapter)
    with pytest.raises(ValueError, match="No tools available"):
        await band_mcp_registrations(adapter)[BandTool.SEND_MESSAGE].execute(
            {
                "content": "from the stopped run",
                "mentions": ["@alice"],
                CHAT_ID_FIELD_NAME: ROOM_ID,
            }
        )
    release_cancel.set()
    client.release.set()
    await adapter.backend_turns.drain()
    return [
        *([NEXT_TURN_REFUSED] if refused else []),
        *([LATE_CALL_LANDED] if stopped.messages_sent else []),
    ]


async def cleanup_waits_for_the_cancel() -> list[str]:
    """Leaving the room waits for the run cancel before it deletes the agent."""
    adapter, client = held_adapter(delete_agents=True)
    release_cancel = await stop_holding_cancel(
        adapter, client, FakeAgentTools(participants=[ALICE])
    )
    cleanup_room = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
    await wait_until(lambda: waiting_on_stop(cleanup_room) or cleanup_room.done())
    release_cancel.set()
    await cleanup_room
    return effects(client)


async def shutdown_waits_for_the_cancel() -> list[str]:
    """Shutdown does not return while a run cancel is still in flight."""
    adapter, client = held_adapter()
    release_cancel = await stop_holding_cancel(
        adapter, client, FakeAgentTools(participants=[ALICE])
    )

    async def shut_down() -> None:
        await adapter.cleanup_all()
        client.journal.append(SHUTDOWN_RETURNED)

    shutdown = asyncio.create_task(shut_down())
    await wait_until(lambda: waiting_on_stop(shutdown) or shutdown.done())
    release_cancel.set()
    await shutdown
    return effects(client)


@looptime.enabled(strict=True)
async def timeout() -> list[str]:
    """The cancel is sent once, before the timeout is reported."""
    adapter, client = held_adapter(timeout_s=TURN_TIMEOUT_SECONDS)
    tools = JournaledTools(client.journal)
    with pytest.raises(TurnResultAlreadyReported):
        await adapter.on_event(turn_input(tools))
    await adapter.backend_turns.drain()
    return effects(client)


async def idle() -> list[str]:
    adapter, client = held_adapter()
    await stop_room(adapter, ROOM_ID)
    return client.cancels


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn-per-room": StopCase(
            run=lambda mode=mode: end_running_turn(mode),
            expected=[CANCEL_AGENT_RUNS],
        )
        for mode in ENDING_CONTROLS
    },
    "stop-mid-turn-shared": StopCase(
        run=lambda: end_running_turn(ControlMode.STOP, shared=True),
        expected=[CANCEL_CONVERSATION_RUNS],
    ),
    "server-rejects-cancel": StopCase(
        run=server_rejects_cancel,
        expected=[
            SEND_AGENT,
            CANCEL_AGENT_RUNS,
            CANCEL_FAILED_WARNING,
            NEXT_TURN_REFUSED,
        ],
    ),
    "late-mcp-call-during-held-cancel": StopCase(
        run=late_mcp_call_during_held_cancel,
        expected=[NEXT_TURN_REFUSED],
    ),
    "cleanup-waits-for-the-cancel": StopCase(
        run=cleanup_waits_for_the_cancel, expected=[CANCEL_AGENT_RUNS, DELETE_AGENT]
    ),
    "shutdown-waits-for-the-cancel": StopCase(
        run=shutdown_waits_for_the_cancel,
        expected=[CANCEL_AGENT_RUNS, SHUTDOWN_RETURNED],
    ),
    "timeout": StopCase(run=timeout, expected=[CANCEL_AGENT_RUNS, FAILURE_POSTED]),
    "idle-room": StopCase(run=idle, expected=[]),
}
