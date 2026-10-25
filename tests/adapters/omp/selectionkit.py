"""Gated native peer and callback for overlapping model transactions."""

from __future__ import annotations

import asyncio

from pydantic import JsonValue

from band.adapters.omp import OmpAdapter, OmpPermissionRequest
from band.core.model_catalog import ModelSelection
from band.integrations.ompwire import OmpCommandName
from tests.adapters.omp.kit import PermissionThenReplyRuntime
from tests.adapters.omp.mailboxpeer import REJECTED_MODEL, UNSUPPORTED_EFFORT

ORIGINAL_MODEL = "original"
ACCEPTED_MODEL = "accepted"
MODEL_PROVIDER = "openai"
SELECTED_EFFORT = "low"


class RollbackRuntime(PermissionThenReplyRuntime):
    """Hold rollback after native state changes but before its response arrives."""

    def __init__(self) -> None:
        super().__init__()
        self.rollback_entered = asyncio.Event()
        self.release_rollback = asyncio.Event()
        self.finish.set()

    async def request(
        self, command: str, **parameters: JsonValue
    ) -> dict[str, JsonValue]:
        rollback = (
            command == OmpCommandName.SET_MODEL
            and parameters.get("modelId") == ORIGINAL_MODEL
            and self.model == (MODEL_PROVIDER, REJECTED_MODEL)
        )
        result = await super().request(command, **parameters)
        if rollback:
            self.rollback_entered.set()
            await self.release_rollback.wait()
        return result


class OverlappingSelections:
    """Issue two public selections from the same permission callback occupancy."""

    def __init__(self, runtime: RollbackRuntime) -> None:
        self.runtime = runtime
        self.adapter: OmpAdapter | None = None
        self.waiter_ready = asyncio.Event()
        self.release = asyncio.Event()
        self.waiter_started = asyncio.Event()
        self.waiter: asyncio.Task[None] | None = None
        self.results: list[object] = []
        self.finished = False

    async def _accepted_selection(self, room_id: str) -> None:
        assert self.adapter is not None
        self.waiter_started.set()
        await self.adapter.apply_model_selection(
            ModelSelection(
                model=f"{MODEL_PROVIDER}/{ACCEPTED_MODEL}",
                reasoning_effort=SELECTED_EFFORT,
            ),
            room_id=room_id,
        )

    async def __call__(self, request: OmpPermissionRequest) -> None:
        if self.finished:
            return
        assert self.adapter is not None
        first = asyncio.create_task(
            self.adapter.apply_model_selection(
                ModelSelection(
                    model=f"{MODEL_PROVIDER}/{REJECTED_MODEL}",
                    reasoning_effort=UNSUPPORTED_EFFORT,
                ),
                room_id=request.room_id,
            )
        )
        try:
            await self.runtime.rollback_entered.wait()
            self.waiter = asyncio.create_task(self._accepted_selection(request.room_id))
            await self.waiter_started.wait()
            self.waiter_ready.set()
            await self.release.wait()
            self.runtime.release_rollback.set()
            self.results = list(
                await asyncio.gather(first, self.waiter, return_exceptions=True)
            )
            self.finished = True
        finally:
            self.runtime.release_rollback.set()
            tasks = [first, *([self.waiter] if self.waiter is not None else [])]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
