"""Backend-stop probes for the letta adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

import band_sdk_core

from band.client.streaming import ControlMode
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_idle_room,
    wait_until,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

AGENT_ID = "agent-1"
CONVERSATION_ID = "conversation-1"
CANCEL_AGENT_RUNS = f"cancel agent {AGENT_ID}"
CANCEL_CONVERSATION_RUNS = f"cancel conversation {CONVERSATION_ID}"
FAILURE_POSTED = "failure posted"
TURN_ENDED = "turn ended"
CANCEL_FAILED_WARNING = "cancel failure logged at WARNING"
# Short enough that the held turn times out within the probe.
PROBE_TURN_TIMEOUT_S = 0.05


class JournaledTools(FakeAgentTools):
    """Room tools that log each failure post into the Letta client's journal."""

    def __init__(self, journal: list[str]) -> None:
        super().__init__()
        self.journal = journal

    async def send_failure(self, failure: band_sdk_core.AgentFailure) -> Any:
        self.journal.append(FAILURE_POSTED)
        return await super().send_failure(failure)


class WarningRecords(logging.Handler):
    """Collects the WARNING records logged while it is attached."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def held_adapter(*, shared: bool = False, timeout_s: float | None = None) -> Any:
    from band.adapters.letta import (  # noqa: PLC0415 -- letta extra, absent from some lane venvs
        LettaAdapterConfig,
    )
    from tests.adapters.lettakit import (  # noqa: PLC0415 -- letta extra, absent from some lane venvs
        RecordingLettaClient,
        ready_letta_adapter,
    )

    settings: dict[str, Any] = {"mode": "shared"} if shared else {}
    if timeout_s is not None:
        settings["turn_timeout_s"] = timeout_s
    adapter, _ = ready_letta_adapter(LettaAdapterConfig(**settings), room_id=ROOM_ID)
    client = RecordingLettaClient()
    client.hold()
    adapter._client = client
    if shared:
        adapter._rooms[ROOM_ID].conversation_id = CONVERSATION_ID
    return adapter, client


async def end_running_turn(mode: ControlMode, *, shared: bool = False) -> list[str]:
    adapter, client = held_adapter(shared=shared)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: bool(client.sent))
    await end_turn(adapter, turn, ROOM_ID, mode)
    return client.cancels


async def server_rejects_cancel() -> list[str]:
    """Without Redis the cancel fails; the turn still ends here."""
    adapter, client = held_adapter()
    client.reject_cancels = True
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: bool(client.sent))
    warnings = WarningRecords()
    logging.getLogger().addHandler(warnings)
    try:
        await end_turn(adapter, turn, ROOM_ID, ControlMode.STOP)
    finally:
        logging.getLogger().removeHandler(warnings)
    outcome = [*client.cancels]
    if turn.cancelled():
        outcome.append(TURN_ENDED)
    if warnings.records:
        outcome.append(CANCEL_FAILED_WARNING)
    return outcome


async def timeout() -> list[str]:
    """The cancel is sent once, before the timeout is reported."""
    adapter, client = held_adapter(timeout_s=PROBE_TURN_TIMEOUT_S)
    tools = JournaledTools(client.journal)
    with suppress(Exception):
        await adapter.on_event(turn_input(tools))
    assert adapter.backend_turns is not None
    await adapter.backend_turns.drain()
    return [entry for entry in client.journal if not entry.startswith("send ")]


async def idle() -> list[str]:
    adapter, client = held_adapter()
    await stop_idle_room(adapter, ROOM_ID)
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
        expected=[CANCEL_AGENT_RUNS, TURN_ENDED, CANCEL_FAILED_WARNING],
    ),
    "timeout": StopCase(run=timeout, expected=[CANCEL_AGENT_RUNS, FAILURE_POSTED]),
    "idle-room": StopCase(run=idle, expected=[]),
}
