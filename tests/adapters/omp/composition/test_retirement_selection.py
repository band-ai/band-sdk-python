"""Unproven callback retirement preserves accepted choices through cleanup retry."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpPermissionRequest
from band.core.model_catalog import ModelSelection
from band.integrations.omp import OMP_FORM_APPROVE
from band.integrations.omprpc import OmpProcessError
from band.runtime.tools import AgentTools
from tests.adapters.lifecycle.runtime import ReplyingPlatform
from tests.adapters.omp.mailboxkit import mailbox_adapter, room_client
from tests.adapters.omp.mailboxpeer import MODEL_PROVIDER, PUBLIC_MODEL, model_reply
from tests.adapters.omp.turns import run_turn
from tests.integrations.omprpc.callbacks import StubbornCallbackObserver
from tests.integrations.omprpc.completion import NO_CALLBACK_GRACE_S
from tests.runtime.helpers import ALICE
from tests.toolkit.tasks import background


class TestRetirementSelection:
    async def test_cleanup_retry_keeps_the_selection_accepted_after_retirement_failed(
        self, tmp_path: Path, tools: AgentTools, platform: ReplyingPlatform
    ) -> None:
        callback = StubbornCallbackObserver()

        async def resolve(_request: OmpPermissionRequest) -> str:
            await callback.hold_callback()
            return OMP_FORM_APPROVE

        adapter = mailbox_adapter(tmp_path, resolve)
        try:
            async with background(run_turn(adapter, tools)) as turn:
                try:
                    await callback.entered.wait()
                    runtime = room_client(adapter)
                    runtime.callback_exit_timeout_s = NO_CALLBACK_GRACE_S
                    turn.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await turn
                    with pytest.raises(OmpProcessError, match="retirement is unproven"):
                        await adapter.on_cleanup("room-123")

                    async with background(
                        adapter.apply_model_selection(
                            ModelSelection(model=f"{MODEL_PROVIDER}/{PUBLIC_MODEL}"),
                            room_id="room-123",
                        )
                    ) as selection:
                        await callback.release()
                        await selection
                    await runtime.close()
                    await adapter.on_cleanup("room-123")
                    follow_up = AgentTools(
                        tools.room_id, tools.rest, participants=[ALICE]
                    )
                    await run_turn(adapter, follow_up)

                    assert platform.replies == [model_reply(PUBLIC_MODEL)]
                finally:
                    await callback.release()
        finally:
            await adapter.cleanup_all()
