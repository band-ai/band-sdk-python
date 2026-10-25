"""Own per-room RPC resources, selections and reentrant occupancy."""

from __future__ import annotations

import asyncio
import collections.abc
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Self

from band.converters.omp import OmpSessionState
from band.core.model_catalog import ModelSelection
from band.core.retention import RetainedRoom
from band.integrations.ompprocess import OmpMcpServer
from band.integrations.omprpc import OmpRpcClient

OMP_SESSION_DIRECTORY = ".band-omp-sessions"
_held_room_gates: ContextVar[frozenset[object]] = ContextVar(
    "omp_held_room_gates", default=frozenset()
)


class RoomGate:
    """Serialize different occupancies while nested and callback tasks reenter."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: object | None = None
        self._depth = 0

    async def __aenter__(self) -> Self:
        occupancy = self._owner
        if occupancy is not None and occupancy in _held_room_gates.get():
            self._depth += 1
            return self
        await self._lock.acquire()
        occupancy = object()
        self._owner = occupancy
        self._depth = 1
        _held_room_gates.set(_held_room_gates.get() | {occupancy})
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._depth -= 1
        if self._depth:
            return
        occupancy = self._owner
        self._owner = None
        if occupancy is not None:
            _held_room_gates.set(_held_room_gates.get() - {occupancy})
        self._lock.release()


def _selection_after(
    prior: ModelSelection | None, selection: ModelSelection
) -> ModelSelection:
    return ModelSelection(
        model=selection.model if selection.model else (prior.model if prior else None),
        reasoning_effort=(
            selection.reasoning_effort
            if selection.reasoning_effort is not None
            else (prior.reasoning_effort if prior else None)
        ),
    )


@dataclass
class OmpRoom:
    room_id: str
    retained: RetainedRoom = field(init=False, repr=False)
    runtime: OmpRpcClient | None = None
    workspace: str = ""
    resources: ExitStack = field(default_factory=ExitStack, repr=False)
    history: OmpSessionState = field(default_factory=OmpSessionState, repr=False)
    servers: collections.abc.Mapping[str, OmpMcpServer] = field(
        default_factory=dict, repr=False
    )
    operation: asyncio.Task[object] | None = field(default=None, repr=False)
    deadline: asyncio.Timeout | None = field(default=None, repr=False)
    disposal: asyncio.Task[None] | None = field(default=None, repr=False)
    clear_selection: bool = False
    session_id: str = ""
    session_file: str | None = None
    bootstrapped: bool = False
    retiring: bool = False
    applied_selection: ModelSelection | None = None

    @property
    def client(self) -> OmpRpcClient:
        """Require the physical connection before native I/O."""
        if self.runtime is None:
            raise RuntimeError("OMP room has no native connection")
        return self.runtime
