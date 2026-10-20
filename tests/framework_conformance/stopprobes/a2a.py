"""Backend-stop probes for the a2a adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import replace

from a2a.types import StreamResponse, TaskState

from band.client.streaming import ControlMode
from band.core.types import HistoryProvider, MessageType
from band.integrations.a2a.adapter import A2AAdapter
from band.integrations.a2a.types import A2AAdapterConfig
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    cancel_turn,
    end_turn,
    stop_room,
    wait_until,
    waiting_on_stop,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input
from tests.integrations.a2a.kit import WORKING, RecordingA2AClient, task_update

TASK_ID = "task-1"
INPUT_REQUIRED = TaskState.Name(TaskState.TASK_STATE_INPUT_REQUIRED)
COMPLETED = TaskState.Name(TaskState.TASK_STATE_COMPLETED)
ANSWER = "the answer"
CANCEL_TASK = f"tasks/cancel {TASK_ID}"
NO_TASK = ""
RESTORED_CANCELLED_TASK = "bootstrap restored the cancelled task"
CLIENT_CLOSED_MID_CANCEL = "client closed while tasks/cancel was in flight"
CLEANUP_RETURNED_MID_CANCEL = "cleanup returned while tasks/cancel was in flight"


def continues(task_id: str) -> str:
    return f"next message continues {task_id or 'no task'}"


def started() -> tuple[A2AAdapter, RecordingA2AClient]:
    adapter = A2AAdapter(A2AAdapterConfig(remote_url="http://a2a.test"))
    client = RecordingA2AClient()
    adapter._client = client
    return adapter, client


def task_in(state_name: str, *, text: str | None = None) -> StreamResponse:
    """A stream event reporting the probe's task in the named ``TaskState``."""
    return task_update(TASK_ID, state_name, text=text)


async def next_message(adapter: A2AAdapter, client: RecordingA2AClient) -> str:
    client.script(task_in(COMPLETED))
    await adapter.on_event(turn_input(FakeAgentTools()))
    return continues(client.continued[-1])


async def end_running_task(mode: ControlMode) -> list[str]:
    adapter, client = started()
    client.script(task_in(WORKING), hold=True)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: ROOM_ID in adapter._tasks)
    await end_turn(adapter, turn, ROOM_ID, mode)
    return [*client.cancelled, await next_message(adapter, client)]


async def stop_before_the_remote_answers(
    mode: ControlMode, answer: StreamResponse
) -> list[str]:
    """A STOP that lands before a new task's first event still reaches the task
    that event reveals, unless that task has already finished."""
    adapter, client = started()
    answered = asyncio.Event()
    client.script(answer, hold=True, gate=answered)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: len(client.continued) == 1)
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, mode)
    answered.set()
    await adapter.backend_turns.drain()
    return [*client.cancelled, await next_message(adapter, client)]


async def stop_while_a_finished_task_is_delivered(
    mode: ControlMode, *, resumed: bool = False
) -> list[str]:
    """A STOP that lands while a finished task's reply is being posted has no
    task left to cancel, whether the task is new or resumed from INPUT_REQUIRED."""
    adapter, client = started()
    if resumed:
        client.script(task_in(INPUT_REQUIRED))
        await adapter.on_event(turn_input(FakeAgentTools()))
    client.script(task_in(COMPLETED, text=ANSWER))
    tools = FakeAgentTools()
    delivering = tools.hold_message(ANSWER)
    turn = asyncio.create_task(adapter.on_event(turn_input(tools)))
    async with delivering:
        await end_turn(adapter, turn, ROOM_ID, mode)
    return [*client.cancelled, await next_message(adapter, client)]


async def stop_continued_task_before_it_answers(mode: ControlMode) -> list[str]:
    """A continued task is cancelled even before the remote's first event."""
    adapter, client = started()
    client.script(task_in(INPUT_REQUIRED))
    await adapter.on_event(turn_input(FakeAgentTools()))
    client.script(hold=True)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: len(client.continued) == 2)
    await end_turn(adapter, turn, ROOM_ID, mode)
    return [*client.cancelled, await next_message(adapter, client)]


def input_required_history() -> list[dict[str, object]]:
    return [
        {
            "message_type": MessageType.TASK,
            "content": f"A2A task {INPUT_REQUIRED}",
            "metadata": {
                "a2a_context_id": "ctx-1",
                "a2a_task_id": TASK_ID,
                "a2a_task_state": INPUT_REQUIRED,
            },
        }
    ]


async def bootstrap_during_cancel() -> list[str]:
    """A bootstrap that starts while tasks/cancel is held does not resume that task."""
    adapter, client = started()
    release = client.hold_cancel()
    client.script(task_in(INPUT_REQUIRED))
    await adapter.on_event(turn_input(FakeAgentTools()))
    client.script(hold=True)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: len(client.continued) == 2)
    turn.cancel()
    with suppress(asyncio.CancelledError):
        await turn
    await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
    await wait_until(lambda: client.cancel_started.is_set())
    client.script(task_in(COMPLETED))
    bootstrap = asyncio.create_task(
        adapter.on_event(
            replace(
                turn_input(FakeAgentTools()),
                history=HistoryProvider(raw=input_required_history()),
            )
        )
    )
    await wait_until(lambda: waiting_on_stop(bootstrap))
    restored = adapter._tasks.get(ROOM_ID) == TASK_ID
    release.set()
    await bootstrap
    return [
        *client.cancelled,
        *([RESTORED_CANCELLED_TASK] if restored else []),
        continues(client.continued[-1]),
    ]


async def stop_holding_cancel() -> tuple[A2AAdapter, RecordingA2AClient, asyncio.Event]:
    """Stop a running task and leave its tasks/cancel in flight until the event is set."""
    adapter, client = started()
    release = client.hold_cancel()
    client.script(task_in(WORKING), hold=True)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: ROOM_ID in adapter._tasks)
    await cancel_turn(turn)
    await adapter.on_interrupt(ROOM_ID, ControlMode.STOP)
    await wait_until(client.cancel_started.is_set)
    return adapter, client, release


async def cleanup_waits_for_the_cancel() -> list[str]:
    """Leaving the room does not return while tasks/cancel is in flight."""
    adapter, client, release = await stop_holding_cancel()
    cleanup = asyncio.create_task(adapter.on_cleanup(ROOM_ID))
    await wait_until(lambda: waiting_on_stop(cleanup) or cleanup.done())
    returned_mid_cancel = cleanup.done()
    release.set()
    await cleanup
    return [
        *client.cancelled,
        *([CLEANUP_RETURNED_MID_CANCEL] if returned_mid_cancel else []),
    ]


async def shutdown_waits_for_the_cancel() -> list[str]:
    """Shutdown does not close the client while tasks/cancel is in flight."""
    adapter, client, release = await stop_holding_cancel()
    shutdown = asyncio.create_task(adapter.cleanup_all())
    await wait_until(lambda: waiting_on_stop(shutdown) or shutdown.done())
    closed_mid_cancel = client.closed
    release.set()
    await shutdown
    return [
        *client.cancelled,
        *([CLIENT_CLOSED_MID_CANCEL] if closed_mid_cancel else []),
    ]


async def input_required_is_not_cancelled() -> list[str]:
    """A task paused for input ends its turn normally and stays continuable."""
    adapter, client = started()
    client.script(task_in(INPUT_REQUIRED))
    await adapter.on_event(turn_input(FakeAgentTools()))
    await stop_room(adapter, ROOM_ID)
    return [*client.cancelled, await next_message(adapter, client)]


async def idle() -> list[str]:
    adapter, client = started()
    await stop_room(adapter, ROOM_ID)
    return client.cancelled


CASES: dict[str, StopCase] = {
    **{
        f"{mode}-mid-turn": StopCase(
            run=lambda mode=mode: end_running_task(mode),
            expected=[CANCEL_TASK, continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-before-a-new-task-answers": StopCase(
            run=lambda mode=mode: stop_before_the_remote_answers(
                mode, task_in(WORKING)
            ),
            expected=[CANCEL_TASK, continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-before-a-finished-task-answers": StopCase(
            run=lambda mode=mode: stop_before_the_remote_answers(
                mode, task_in(COMPLETED)
            ),
            expected=[continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-while-a-finished-task-is-delivered": StopCase(
            run=lambda mode=mode: stop_while_a_finished_task_is_delivered(mode),
            expected=[continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-while-a-continued-task-finishes": StopCase(
            run=lambda mode=mode: stop_while_a_finished_task_is_delivered(
                mode, resumed=True
            ),
            expected=[continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    **{
        f"{mode}-continued-task-before-it-answers": StopCase(
            run=lambda mode=mode: stop_continued_task_before_it_answers(mode),
            expected=[CANCEL_TASK, continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    "input-required-is-not-cancelled": StopCase(
        run=input_required_is_not_cancelled, expected=[continues(TASK_ID)]
    ),
    "bootstrap-during-cancel": StopCase(
        run=bootstrap_during_cancel,
        expected=[CANCEL_TASK, continues(NO_TASK)],
    ),
    "cleanup-waits-for-the-cancel": StopCase(
        run=cleanup_waits_for_the_cancel, expected=[CANCEL_TASK]
    ),
    "shutdown-waits-for-the-cancel": StopCase(
        run=shutdown_waits_for_the_cancel, expected=[CANCEL_TASK]
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
