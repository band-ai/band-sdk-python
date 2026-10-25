"""Callback identity registration precedes eager execution of native requests."""

from __future__ import annotations

import asyncio

import pytest

from tests.conftest import eager_tasks, requires_eager_tasks
from tests.integrations.omprpc.callbackkit import (
    ImmediateCallbacks,
    callback_client,
    callback_results,
)
from tests.integrations.omprpc.callbackpeer import FINISH, NEW_RESULT, CallbackKind
from tests.integrations.omprpc.kit import Observer, checkpoint_frames, second_prompt


class TestCallbackStartup:
    @requires_eager_tasks
    @pytest.mark.parametrize("kind", list(CallbackKind))
    @pytest.mark.parametrize(
        "scheduled", [False, True], ids=["standalone", "scheduled"]
    )
    async def test_eager_callback_can_respond_before_task_creation_returns(
        self, kind: CallbackKind, scheduled: bool
    ) -> None:
        async with (
            asyncio.timeout(10),
            callback_client(kind, scheduled) as (client, _mailbox),
        ):
            callbacks = ImmediateCallbacks()
            with eager_tasks():
                running = asyncio.create_task(
                    client.prompt(
                        "first",
                        on_event=Observer().on_event,
                        on_tool=callbacks.on_tool,
                        on_ui=callbacks.on_ui,
                    )
                )
                try:
                    await callbacks.completed.wait()
                    assert callback_results(await checkpoint_frames(client)) == [
                        NEW_RESULT
                    ]
                    await client.request(FINISH)
                    await running
                    await second_prompt(client)
                finally:
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
