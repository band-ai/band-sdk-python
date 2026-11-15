"""Observe spawned selection rejection alongside direct callback reentry."""

from __future__ import annotations

import asyncio

from band.adapters.omp import OmpPermissionRequest
from band.core.model_catalog import ModelSelection
from band.runtime.mailbox import NestedMailboxAsk
from tests.adapters.omp.mailboxkit import CallbackResolver
from tests.adapters.omp.mailboxpeer import MODEL_PROVIDER, PUBLIC_MODEL


class ChildSelectingResolver(CallbackResolver):
    def __init__(self) -> None:
        super().__init__()
        self.child_rejected = False

    async def __call__(self, request: OmpPermissionRequest) -> str:
        assert self.adapter is not None
        child = asyncio.create_task(
            self.adapter.apply_model_selection(
                ModelSelection(model=f"{MODEL_PROVIDER}/{PUBLIC_MODEL}"),
                room_id=request.room_id,
            )
        )
        try:
            await child
        except NestedMailboxAsk:
            self.child_rejected = True
        return await super().__call__(request)
