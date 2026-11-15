"""Cancel an intentionally stopped prompt while its native abort is unacknowledged."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from band.integrations.omprpc import OmpRpcClient
from band.integrations.ompwire import OmpHostToolCall, OmpToolResult
from tests.adapters.omp.declinepeer import (
    DECLINE_TRANSCRIPT,
    STOP_CHECKPOINT,
    DeclineScenario,
)
from tests.integrations.omppeers.base import COMPLETE
from tests.integrations.omprpc.events import EventRecorder, follow_up_texts
from tests.integrations.omprpc.processes import owned_rpc_process
from tests.paths import REPO_ROOT


class TestTerminalStop:
    async def test_cancel_pending_stop_reaps_process_and_prompt_tasks(
        self, tmp_path: Path
    ) -> None:
        client = OmpRpcClient(
            (
                sys.executable,
                "-u",
                str(REPO_ROOT / "tests" / "adapters" / "omp" / "declinepeer.py"),
                DeclineScenario.HELD_STOP,
                str(tmp_path / DECLINE_TRANSCRIPT),
            ),
            env={"PYTHONPATH": str(REPO_ROOT)},
        )
        observer = EventRecorder()

        async def on_tool(call: OmpHostToolCall) -> OmpToolResult:
            client.stop_after_tool(call.id)
            client.stop_after_tool(call.id)
            return OmpToolResult(content=[])

        await client.start()
        process = owned_rpc_process(client)
        prior_tasks = asyncio.all_tasks()
        prompt = asyncio.create_task(
            client.prompt(
                "first",
                on_event=observer.on_event,
                on_tool=on_tool,
                on_ui=observer.on_ui,
            )
        )
        try:
            await client.request(STOP_CHECKPOINT)
            prompt_tasks = asyncio.all_tasks() - prior_tasks
            assert not prompt.done()

            prompt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await prompt

            assert process.returncode is not None
            assert all(task.done() for task in prompt_tasks)

            await client.start()
            assert await follow_up_texts(client) == [COMPLETE]
        finally:
            prompt.cancel()
            await asyncio.gather(prompt, return_exceptions=True)
            await client.close()
