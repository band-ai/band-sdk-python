"""Real resource-backed work held at explicit invocation boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast
from uuid import uuid4

import httpx
from pydantic import BaseModel

from tests.e2e.baseline.toolkit.tools import ToolSpec

LOOKUP_PATH = "/users/1"


class Operation(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class ActivityLookupInput(BaseModel):
    """Retrieve a fresh opaque result for the requested operation."""

    key: Operation
    private_note: str


@dataclass
class Invocation:
    started: bool = False
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    result: str = ""


class ActivityWork:
    def __init__(
        self, client: httpx.AsyncClient, base_url: str, *, deadline_s: float
    ) -> None:
        self._client, self._base_url = client, base_url
        self.deadline_s = deadline_s
        self.note = uuid4().hex
        self.operations = {key: Invocation() for key in Operation}
        self.tool = ToolSpec(ActivityLookupInput, self.execute)

    def __getitem__(self, key: Operation) -> Invocation:
        return self.operations[key]

    async def execute(self, args: BaseModel) -> str:
        request = cast(ActivityLookupInput, args)
        if request.private_note != self.note:
            raise ValueError("The operation requires its current private note")
        work = self.operations[request.key]
        if work.started:
            raise ValueError(f"Operation {request.key.value} already has an invocation")
        work.started = True
        try:
            response = await self._client.get(f"{self._base_url}{LOOKUP_PATH}")
            response.raise_for_status()
            work.result = f"{response.json()['name']}-{uuid4().hex}"
            work.entered.set()
            await work.release.wait()
            return work.result
        finally:
            work.settled.set()

    def instruction(self, key: Operation) -> str:
        return f"Call {self.tool.name} now with key={key.value} and private_note={self.note}. Report only its fresh result to this room."

    async def entered(self, key: Operation) -> None:
        async with asyncio.timeout(self.deadline_s):
            await self[key].entered.wait()

    async def settled(self, key: Operation) -> None:
        async with asyncio.timeout(self.deadline_s):
            await self[key].settled.wait()

    @asynccontextmanager
    async def releasing_on_exit(self) -> AsyncIterator[None]:
        try:
            yield
        finally:
            for work in self.operations.values():
                work.release.set()
            for key, work in self.operations.items():
                if work.started:
                    await self.settled(key)
