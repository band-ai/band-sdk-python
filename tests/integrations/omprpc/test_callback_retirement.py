"""Callbacks admitted during settlement share the original retirement deadline."""

from __future__ import annotations

import asyncio

import pytest

from band.integrations.omprpc import OmpProcessError
from band.integrations.ompwire import (
    OmpCommandName,
    OmpEventType,
    OmpHostToolCall,
    OmpToolResult,
)
from tests.integrations.omppeer import (
    LATE_CALLBACK_ID,
    SETTLED_CALLBACK_ID,
    SETTLED_CALLBACK_SCENARIO,
)
from tests.integrations.omprpc.cancellation import HeldCleanup
from tests.integrations.omprpc.kit import (
    ClientFactory,
    Observer,
    checkpoint_frames,
    owned_rpc_process,
)
from tests.toolkit.clock import binary_clock_resolution
from tests.toolkit.timecontrol import controlled_clock


class TestCallbackRetirement:
    @pytest.mark.parametrize(
        "retire_second_on_first",
        [False, True],
        ids=["held-late-callback", "callback-turnover"],
    )
    @pytest.mark.looptime(resolution=binary_clock_resolution())
    async def test_a_callback_admitted_during_the_join_cannot_escape_the_deadline(
        self, client_factory: ClientFactory, retire_second_on_first: bool
    ) -> None:
        async with controlled_clock() as clock:
            client = await clock.run_io(client_factory(SETTLED_CALLBACK_SCENARIO))
            grace = 1.0
            client.callback_exit_timeout_s = grace
            process = owned_rpc_process(client)
            observer = Observer()
            cleanup = HeldCleanup(client)
            joining: asyncio.Task[None] | None = None
            first_started, first_retired, second_started = (
                asyncio.Event() for _ in range(3)
            )
            release_first, release_second = (asyncio.Event() for _ in range(2))
            second_cancelled = asyncio.Event()

            async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
                if call.id == SETTLED_CALLBACK_ID:
                    task = asyncio.current_task()
                    assert task is not None
                    task.add_done_callback(lambda _done: first_retired.set())
                    first_started.set()
                    await release_first.wait()
                else:
                    assert call.id == LATE_CALLBACK_ID
                    second_started.set()
                    try:
                        await (
                            first_retired.wait()
                            if retire_second_on_first
                            else release_second.wait()
                        )
                    except asyncio.CancelledError:
                        second_cancelled.set()
                        raise
                return OmpToolResult(content=[])

            async with clock.background(
                client.prompt(
                    "first",
                    on_event=observer.on_event,
                    on_tool=on_tool,
                    on_ui=observer.on_ui,
                )
            ) as running:
                try:
                    await first_started.wait()
                    await observer.wait_for(OmpEventType.AGENT_END)
                    frames = await checkpoint_frames(client)
                    assert any(
                        frame["type"] == OmpCommandName.GET_STATE for frame in frames
                    )
                    await client.request("release")
                    await second_started.wait()
                    if retire_second_on_first:
                        clock.advance(grace)
                    release_first.set()
                    await first_retired.wait()
                    if not retire_second_on_first:
                        clock.advance(grace)
                    joining = asyncio.create_task(cleanup.wait_for_join(running))
                    done, _ = await asyncio.wait(
                        (running, joining), return_when=asyncio.FIRST_COMPLETED
                    )
                    assert joining in done
                    await joining
                    assert process.returncode is None
                    cleanup.release.set()
                    with pytest.raises(OmpProcessError):
                        await clock.run_io(running)
                    if not retire_second_on_first:
                        assert second_cancelled.is_set()
                    assert process.returncode is not None
                finally:
                    release_first.set()
                    release_second.set()
                    cleanup.release.set()
                    if joining is not None:
                        joining.cancel()
                        await asyncio.gather(joining, return_exceptions=True)
