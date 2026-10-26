"""Announce admission of a prompt queued behind an active turn."""

from __future__ import annotations

import asyncio

from band.integrations.omprpc import OmpRpcClient
from tests.integrations.omprpc.events import EventRecorder


async def announced_prompt(
    client: OmpRpcClient,
    observer: EventRecorder,
    entered: asyncio.Event,
    message: str,
) -> None:
    entered.set()
    await client.prompt(
        message,
        on_event=observer.on_event,
        on_tool=observer.on_tool,
        on_ui=observer.on_ui,
    )


async def queue_prompt(
    client: OmpRpcClient, message: str = "queued"
) -> asyncio.Task[None]:
    entered = asyncio.Event()
    task = asyncio.create_task(
        announced_prompt(client, EventRecorder(), entered, message)
    )
    # The coroutine reaches the occupied admission lock before this waiter resumes.
    await entered.wait()
    return task
