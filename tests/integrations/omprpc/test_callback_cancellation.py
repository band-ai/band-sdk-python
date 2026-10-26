"""Native cancellation settles running callbacks scheduled through a mailbox."""

from __future__ import annotations

import asyncio
import gc

import pytest

from tests.integrations.omppeers.base import COMPLETE
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
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.wirelog import checkpoint_frames


class TestCallbackCancellation:
    @pytest.mark.parametrize("kind", list(CallbackKind))
    async def test_running_callback_settles_without_a_response(
        self, kind: CallbackKind, callback_warnings: pytest.WarningsRecorder
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
                    on_event=EventRecorder().on_event,
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
                assert await follow_up_texts(client) == [COMPLETE]
                gc.collect()
                assert not callback_warnings
            finally:
                old.release.set()
                running.cancel()
                await asyncio.gather(running, return_exceptions=True)
