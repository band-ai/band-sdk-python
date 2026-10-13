"""Backend-stop probes for the a2a adapter; see ``stopprobes``."""

from __future__ import annotations

import asyncio
from typing import Any

from band.client.streaming import ControlMode
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.stopprobes import (
    ENDING_CONTROLS,
    StopCase,
    end_turn,
    stop_room,
    wait_until,
)
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input

TASK_ID = "task-1"
CANCEL_TASK = f"tasks/cancel {TASK_ID}"
NO_TASK = ""


def continues(task_id: str) -> str:
    return f"next message continues {task_id or 'no task'}"


def started() -> tuple[Any, Any]:
    from band.integrations.a2a.adapter import (  # noqa: PLC0415 -- a2a extra, absent from some lane venvs
        A2AAdapter,
    )
    from band.integrations.a2a.types import (  # noqa: PLC0415 -- a2a extra, absent from some lane venvs
        A2AAdapterConfig,
    )
    from tests.integrations.a2a.kit import (  # noqa: PLC0415 -- a2a extra, absent from some lane venvs
        RecordingA2AClient,
    )

    adapter = A2AAdapter(A2AAdapterConfig(remote_url="http://a2a.test"))
    client = RecordingA2AClient()
    adapter._client = client
    return adapter, client


def task_in(state_name: str) -> Any:
    """A stream event reporting the probe's task in the named ``TaskState``."""
    from tests.integrations.a2a.kit import (  # noqa: PLC0415 -- a2a extra, absent from some lane venvs
        task_update,
    )

    return task_update(TASK_ID, state_name)


async def next_message(adapter: Any, client: Any) -> str:
    client.script(task_in("TASK_STATE_COMPLETED"))
    await adapter.on_event(turn_input(FakeAgentTools()))
    return continues(client.continued[-1])


async def end_running_task(mode: ControlMode) -> list[str]:
    adapter, client = started()
    client.script(task_in("TASK_STATE_WORKING"), hold=True)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: ROOM_ID in adapter._tasks)
    await end_turn(adapter, turn, ROOM_ID, mode)
    return [*client.cancelled, await next_message(adapter, client)]


async def stop_continued_task_before_it_answers(mode: ControlMode) -> list[str]:
    """A continued task is cancelled even before the remote's first event."""
    adapter, client = started()
    client.script(task_in("TASK_STATE_INPUT_REQUIRED"))
    await adapter.on_event(turn_input(FakeAgentTools()))
    client.script(hold=True)
    turn = asyncio.create_task(adapter.on_event(turn_input(FakeAgentTools())))
    await wait_until(lambda: len(client.continued) == 2)
    await end_turn(adapter, turn, ROOM_ID, mode)
    return [*client.cancelled, await next_message(adapter, client)]


async def input_required_is_not_cancelled() -> list[str]:
    """A task paused for input ends its turn normally and stays continuable."""
    adapter, client = started()
    client.script(task_in("TASK_STATE_INPUT_REQUIRED"))
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
        f"{mode}-continued-task-before-it-answers": StopCase(
            run=lambda mode=mode: stop_continued_task_before_it_answers(mode),
            expected=[CANCEL_TASK, continues(NO_TASK)],
        )
        for mode in ENDING_CONTROLS
    },
    "input-required-is-not-cancelled": StopCase(
        run=input_required_is_not_cancelled, expected=[continues(TASK_ID)]
    ),
    "idle-room": StopCase(run=idle, expected=[]),
}
