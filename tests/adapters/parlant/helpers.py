"""Shared fakes and sample values for the ParlantAdapter tests."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel

from tests.runtime.currentrecords import current_records

MESSAGE_KIND = "message"
AI_AGENT_SOURCE = "ai_agent"
# The session the mock Application creates, and who sends the sample message.
SESSION_ID = "session-123"
SENDER_ID = "user-456"
SENDER_NAME = "Alice"
# The Band agent's own name and description, handed to on_started.
BAND_NAME = "BandName"
BAND_DESCRIPTION = "Band description"


def current_content(posted: str) -> str | None:
    """The content of the one current input a customer message carries."""
    (record,) = current_records(posted)
    return record["content"]


class LookupInput(BaseModel):
    """Look a code up."""

    code: str


async def lookup(args: LookupInput) -> str:
    return args.code


LOOKUP = (LookupInput, lookup)


def agent_event(
    message: str, *, offset: int, tags: list[str] | None = None
) -> MagicMock:
    """A Parlant AI-agent MESSAGE event as the response wait reads it."""
    event = MagicMock()
    event.kind = MESSAGE_KIND
    event.source = AI_AGENT_SOURCE
    event.offset = offset
    event.data = {"message": message, "tags": tags or []}
    return event


class RecordingBackgroundTasks:
    """Parlant's background-task service, recording each cancelled tag."""

    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(
        self, coroutine: Coroutine[Any, Any, None], /, *, tag: str
    ) -> asyncio.Task[None]:
        existing = self._tasks.get(tag)
        if existing is not None and not existing.done():
            raise RuntimeError(f"Task {tag!r} is already running")
        task = asyncio.create_task(coroutine)
        self._tasks[tag] = task
        return task

    async def cancel(self, *, tag: str, reason: str = "(not given)") -> None:
        self.cancelled.append(tag)
        task = self._tasks.get(tag)
        if task is not None and not task.done():
            task.cancel(reason)
