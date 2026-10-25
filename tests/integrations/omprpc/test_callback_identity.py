"""Cancelled callback invocations cannot respond under a replacement's ID."""

from __future__ import annotations

import asyncio
import gc

import pytest

from tests.integrations.omprpc.callbackkit import (
    CallbackGate,
    HeldCallbacks,
    callback_client,
    callback_results,
)
from tests.integrations.omprpc.callbackpeer import (
    CANCEL,
    FINISH,
    NEW_RESULT,
    OLD_RESULT,
    REPLACE,
    CallbackKind,
)
from tests.integrations.omprpc.kit import Observer, checkpoint_frames, second_prompt


class TestCallbackIdentity:
    @pytest.mark.parametrize("kind", list(CallbackKind))
    @pytest.mark.parametrize(
        "scheduled", [False, True], ids=["standalone", "scheduled"]
    )
    async def test_cancelled_handler_cannot_answer_reused_id(
        self, kind: CallbackKind, scheduled: bool, recwarn: pytest.WarningsRecorder
    ) -> None:
        async with (
            asyncio.timeout(10),
            callback_client(kind, scheduled) as client,
        ):
            old = CallbackGate(OLD_RESULT)
            replacement = CallbackGate(NEW_RESULT)
            callbacks = HeldCallbacks(old, replacement)
            running = asyncio.create_task(
                client.prompt(
                    "first",
                    on_event=Observer().on_event,
                    on_tool=callbacks.on_tool,
                    on_ui=callbacks.on_ui,
                )
            )
            try:
                await old.entered.wait()
                await client.request(CANCEL)
                await old.cancelled.wait()
                await client.request(REPLACE)
                await old.finish()
                await replacement.entered.wait()
                assert callback_results(await checkpoint_frames(client)) == []
                await replacement.finish()
                assert callback_results(await checkpoint_frames(client)) == [NEW_RESULT]
                await client.request(FINISH)
                await running
                await second_prompt(client)
                gc.collect()
                assert not recwarn
            finally:
                old.release.set()
                replacement.release.set()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
