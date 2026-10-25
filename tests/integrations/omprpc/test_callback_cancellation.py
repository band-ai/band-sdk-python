"""Native cancellation settles running callbacks scheduled through a mailbox."""

from __future__ import annotations

import asyncio
import gc

import pytest

from tests.integrations.omprpc.callbackkit import (
    HeldCallbacks,
    callback_client,
    callback_results,
)
from tests.integrations.omprpc.callbackpeer import CANCEL, FINISH, CallbackKind
from tests.integrations.omprpc.kit import Observer, checkpoint_frames, second_prompt


class TestCallbackCancellation:
    @pytest.mark.parametrize("kind", list(CallbackKind))
    async def test_running_callback_settles_without_a_response(
        self, kind: CallbackKind, recwarn: pytest.WarningsRecorder
    ) -> None:
        async with (
            asyncio.timeout(10),
            callback_client(kind, scheduled=True) as (client, mailbox),
        ):
            callbacks = HeldCallbacks()
            running = asyncio.create_task(
                client.prompt(
                    "first",
                    on_event=Observer().on_event,
                    on_tool=callbacks.on_tool,
                    on_ui=callbacks.on_ui,
                )
            )
            try:
                await mailbox.admitted.get()
                await callbacks.entered.wait()
                await client.request(CANCEL)
                await callbacks.cancelled.wait()
                assert not callbacks.tasks[0].done()
                await callbacks.settle_old()
                assert callback_results(await checkpoint_frames(client)) == []
                await client.request(FINISH)
                await running
                await second_prompt(client)
                gc.collect()
                assert not recwarn
            finally:
                await callbacks.close()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
