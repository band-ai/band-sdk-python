"""Record native events and return delivered prompt text."""

from __future__ import annotations

import asyncio

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import (
    OmpEvent,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
    OmpUIRequest,
    OmpUIResponse,
)
from tests.integrations.omppeers.base import FOLLOW_UP_PROMPT


class EventRecorder:
    def __init__(self) -> None:
        self.events: list[OmpEvent] = []
        self.queue: asyncio.Queue[OmpEvent] = asyncio.Queue()

    @property
    def assistant_texts(self) -> list[str]:
        return [
            part.text
            for event in self.events
            if event.message is not None
            and event.message.role == "assistant"
            and isinstance(event.message.content, list)
            for part in event.message.content
            if part.text is not None
        ]

    @property
    def outline(self) -> list[OmpEventType]:
        return [event.type for event in self.events]

    @property
    def update_ids(self) -> list[str | None]:
        return [
            event.messageId
            for event in self.events
            if event.type == OmpEventType.MESSAGE_UPDATE
        ]

    async def on_event(self, event: OmpEvent) -> None:
        self.events.append(event)
        self.queue.put_nowait(event)

    async def on_tool(self, call: OmpHostToolCall) -> OmpToolResult:
        raise AssertionError(f"Unexpected host tool callback: {call.toolName}")

    async def on_ui(self, request: OmpUIRequest) -> OmpUIResponse:
        raise AssertionError(f"Unexpected UI callback: {request.method}")

    async def wait_for(self, event_type: OmpEventType) -> OmpEvent:
        async with asyncio.timeout(5):
            while True:
                event = await self.queue.get()
                if event.type == event_type:
                    return event


async def record_prompt(
    client: OmpRpcClient, observer: EventRecorder, message: str = "first"
) -> None:
    await client.prompt(
        message,
        on_event=observer.on_event,
        on_tool=observer.on_tool,
        on_ui=observer.on_ui,
    )


async def follow_up_texts(client: OmpRpcClient) -> list[str]:
    observer = EventRecorder()
    await record_prompt(client, observer, FOLLOW_UP_PROMPT)
    return observer.assistant_texts
