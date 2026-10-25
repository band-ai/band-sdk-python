"""Native cancellation settles running callbacks scheduled through a mailbox."""

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
    OLD_RESULT,
    CallbackKind,
)
from tests.integrations.omprpc.kit import Observer, checkpoint_frames, second_prompt


class TestCallbackCancellation:
    @pytest.mark.parametrize("kind", list(CallbackKind))
    async def test_running_callback_settles_without_a_response(
        self, kind: CallbackKind, recwarn: pytest.WarningsRecorder
    ) -> None:
        async with (
            asyncio.timeout(10),
            callback_client(kind, scheduled=True) as client,
        ):
            old = CallbackGate(OLD_RESULT)
            callbacks = HeldCallbacks(old)
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
                await old.finish()
                assert callback_results(await checkpoint_frames(client)) == []
                await client.request(FINISH)
                await running
                await second_prompt(client)
                gc.collect()
                assert not recwarn
            finally:
                old.release.set()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
