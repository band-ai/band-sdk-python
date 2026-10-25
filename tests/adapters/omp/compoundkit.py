"""Real adapter operations held across native cancellation boundaries."""

from __future__ import annotations

import asyncio
from typing import Any

from band.adapters.omp import OmpAdapter, OmpPermissionRequest
from band.core.model_catalog import ModelSelection
from band.integrations.omp import OMP_FORM_APPROVE
from band.runtime.tools import BandTool, ToolCallOutcome
from tests.adapters.hold import Hold
from tests.adapters.omp.mailboxkit import MailboxTools
from tests.adapters.omp.mailboxpeer import (
    MODEL_PROVIDER,
    REJECTED_MODEL,
    UNSUPPORTED_EFFORT,
)


class RollbackPermission:
    """Run a partial native selection inside the actual mailbox callback."""

    def __init__(self) -> None:
        self.adapter: OmpAdapter | None = None
        self.calls: list[str] = []
        self.attempted = False
        self.finished = asyncio.Event()

    async def __call__(self, request: OmpPermissionRequest) -> str:
        self.calls.append(request.title)
        if not self.attempted:
            self.attempted = True
            assert self.adapter is not None
            try:
                await self.adapter.apply_model_selection(
                    ModelSelection(
                        model=f"{MODEL_PROVIDER}/{REJECTED_MODEL}",
                        reasoning_effort=UNSUPPORTED_EFFORT,
                    ),
                    room_id=request.room_id,
                )
            finally:
                self.finished.set()
        return OMP_FORM_APPROVE


class SettlingTools(MailboxTools):
    """Keep a real Band tool alive until its cancelled work has settled."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.settling = Hold()
        self.finished = asyncio.Event()
        self.calls: list[str] = []

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        self.calls.append(tool_name)
        if tool_name == BandTool.GET_PARTICIPANTS:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.settling.wait()
            finally:
                self.finished.set()
        return await super().execute_tool_call_structured(tool_name, arguments)
