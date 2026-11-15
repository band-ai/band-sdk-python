"""Own per-room RPC resources and selections."""

from __future__ import annotations

import asyncio
import collections.abc
from contextlib import ExitStack
from dataclasses import dataclass, field

from band.converters.omp import OmpSessionState
from band.core.model_catalog import ModelSelection
from band.core.retention import RetainedRoom
from band.integrations.ompprocess import OmpMcpServer
from band.integrations.omprpc import OmpRpcClient
from band.runtime.sessionresume import SessionSeed

OMP_SESSION_DIRECTORY = ".band-omp-sessions"


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
    seed: SessionSeed = field(default_factory=SessionSeed)
    retiring: bool = False
    applied_selection: ModelSelection | None = None

    @property
    def client(self) -> OmpRpcClient:
        """Require the physical connection before native I/O."""
        if self.runtime is None:
            raise RuntimeError("OMP room has no native connection")
        return self.runtime
