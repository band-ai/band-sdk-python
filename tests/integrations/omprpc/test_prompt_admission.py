"""Prompt writes serialize without keeping acceptance or callbacks on the worker."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from functools import partial
from pathlib import Path

import pytest

from band.integrations.ompwire import OmpCommandName
from band.runtime.mailbox import RoomMailbox
from tests.adapters.hold import Hold
from tests.conftest import eager_tasks, requires_eager_tasks
from tests.integrations.omprpc.callbackkit import (
    ROOM,
    ImmediateCallbacks,
    callback_client,
    held_prompt_admission,
    received_commands,
)
from tests.integrations.omprpc.callbackpeer import (
    FINISH,
    HOLD_ACCEPTANCE,
    RELEASE_ACCEPTANCE,
    CallbackKind,
)
from tests.integrations.omprpc.events import EventRecorder
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.integrations.omprpc.wirelog import checkpoint_frames


@pytest.mark.parametrize(
    "tasks",
    [nullcontext, pytest.param(eager_tasks, marks=requires_eager_tasks)],
    ids=["regular", "eager"],
)
class TestPromptAdmission:
    async def test_cancelling_a_queued_admission_does_not_wait_for_other_work(
        self, tmp_path: Path, tasks: Callable[[], AbstractContextManager[None]]
    ) -> None:
        transcript = tmp_path / "queued-admission.jsonl"
        mailbox = RoomMailbox()
        held = Hold()
        queued = asyncio.Event()
        callbacks, observer = ImmediateCallbacks(), EventRecorder()

        async def admission(write: Callable[[], Awaitable[None]]) -> None:
            queued.set()
            await mailbox.ask(ROOM, write)

        async with callback_client(
            CallbackKind.HOST, False, transcript=transcript
        ) as client:
            process = owned_rpc_process(client)
            with tasks():
                blocker = asyncio.create_task(mailbox.ask(ROOM, held.wait))
                prompt: asyncio.Task[None] | None = None
                try:
                    await held.arrived()
                    prompt = asyncio.create_task(
                        client.prompt(
                            "first",
                            on_event=observer.on_event,
                            on_tool=callbacks.on_tool,
                            on_ui=callbacks.on_ui,
                            schedule_admission=admission,
                        )
                    )
                    await queued.wait()
                    prompt.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await prompt
                    assert not held.released.is_set() and not blocker.done()
                    assert process.returncode is not None
                    held.released.set()
                    await blocker
                    await mailbox.close(ROOM)
                    assert received_commands(transcript) == []
                finally:
                    held.released.set()
                    pending = [blocker, *([prompt] if prompt is not None else [])]
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await mailbox.close(ROOM)

    async def test_write_precedes_successors_and_acceptance_waits_off_worker(
        self, tmp_path: Path, tasks: Callable[[], AbstractContextManager[None]]
    ) -> None:
        transcript = tmp_path / "admission.jsonl"
        mailbox = RoomMailbox()
        held = Hold()
        queued = asyncio.Event()
        callbacks, observer = ImmediateCallbacks(), EventRecorder()

        async with callback_client(
            CallbackKind.HOST, False, transcript=transcript
        ) as client:
            client.schedule_callback = partial(mailbox.ask, ROOM)
            await client.request(HOLD_ACCEPTANCE)

            async def successor() -> None:
                queued.set()
                await mailbox.ask(
                    ROOM,
                    partial(client.request, OmpCommandName.SET_HOST_TOOLS, tools=[]),
                )

            with tasks():
                prompt = asyncio.create_task(
                    client.prompt(
                        "first",
                        on_event=observer.on_event,
                        on_tool=callbacks.on_tool,
                        on_ui=callbacks.on_ui,
                        schedule_admission=partial(
                            held_prompt_admission, mailbox, held
                        ),
                    )
                )
                following: asyncio.Task[None] | None = None
                try:
                    await held.arrived()
                    following = asyncio.create_task(successor())
                    await queued.wait()
                    held.released.set()
                    await following
                    await callbacks.completed.wait()
                    await checkpoint_frames(client)
                    assert received_commands(transcript) == [
                        OmpCommandName.PROMPT,
                        OmpCommandName.SET_HOST_TOOLS,
                    ]
                    assert not prompt.done()
                    await client.request(RELEASE_ACCEPTANCE)
                    await client.request(FINISH)
                    await prompt
                finally:
                    held.released.set()
                    pending = [prompt, *([following] if following is not None else [])]
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await mailbox.close(ROOM)

    async def test_cancelled_admission_never_writes_a_prompt(
        self, tmp_path: Path, tasks: Callable[[], AbstractContextManager[None]]
    ) -> None:
        transcript = tmp_path / "cancelled-admission.jsonl"
        mailbox = RoomMailbox()
        held = Hold()
        callbacks, observer = ImmediateCallbacks(), EventRecorder()

        async with callback_client(
            CallbackKind.HOST, False, transcript=transcript
        ) as client:
            process = owned_rpc_process(client)
            with tasks():
                prompt = asyncio.create_task(
                    client.prompt(
                        "first",
                        on_event=observer.on_event,
                        on_tool=callbacks.on_tool,
                        on_ui=callbacks.on_ui,
                        schedule_admission=partial(
                            held_prompt_admission, mailbox, held
                        ),
                    )
                )
                try:
                    await held.arrived()
                    prompt.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await prompt
                    assert process.returncode is not None
                    assert received_commands(transcript) == []
                finally:
                    held.released.set()
                    prompt.cancel()
                    await asyncio.gather(prompt, return_exceptions=True)
                    await mailbox.close(ROOM)

    async def test_cancellation_after_the_write_reaps_and_allows_a_default_prompt(
        self, tmp_path: Path, tasks: Callable[[], AbstractContextManager[None]]
    ) -> None:
        transcript = tmp_path / "cancelled-acceptance.jsonl"
        mailbox = RoomMailbox()
        callbacks, observer = ImmediateCallbacks(), EventRecorder()
        async with callback_client(
            CallbackKind.HOST, False, transcript=transcript
        ) as client:
            process = owned_rpc_process(client)
            await client.request(HOLD_ACCEPTANCE)
            with tasks():
                prompt = asyncio.create_task(
                    client.prompt(
                        "first",
                        on_event=observer.on_event,
                        on_tool=callbacks.on_tool,
                        on_ui=callbacks.on_ui,
                        schedule_admission=partial(mailbox.ask, ROOM),
                    )
                )
                try:
                    await callbacks.completed.wait()
                    await checkpoint_frames(client)
                    prompt.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await prompt
                    assert process.returncode is not None
                    assert received_commands(transcript) == [OmpCommandName.PROMPT]
                    await client.start()
                    callbacks.completed.clear()
                    prompt = asyncio.create_task(
                        client.prompt(
                            "second",
                            on_event=observer.on_event,
                            on_tool=callbacks.on_tool,
                            on_ui=callbacks.on_ui,
                        )
                    )
                    await callbacks.completed.wait()
                    await client.request(FINISH)
                    await prompt
                    assert received_commands(transcript) == [
                        OmpCommandName.PROMPT,
                        OmpCommandName.PROMPT,
                    ]
                finally:
                    prompt.cancel()
                    await asyncio.gather(prompt, return_exceptions=True)
                    await mailbox.close(ROOM)
