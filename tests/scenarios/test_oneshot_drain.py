"""A one-shot host shutting down while a turn and a room removal are in flight."""

from __future__ import annotations

import asyncio

import pytest

from tests.runtime.oneshot.kit import run_ready_callbacks
from tests.scenarios.oneshot import OneShotRig


class TestOneShotDrain:
    async def test_shutdown_drains_accepted_work_before_disposal(
        self, oneshot: OneShotRig
    ) -> None:
        oneshot.adapter.hold("work")
        accepted = oneshot.deliver("r1", "work")
        await oneshot.observations.until(lambda: "r1: turn work" in oneshot.journal)
        assert (await oneshot.remove_room("r1"))["status"] == "cleaned_up"

        stopping = asyncio.create_task(oneshot.invoker.shutdown())
        await run_ready_callbacks()
        with pytest.raises(RuntimeError, match="shut down"):
            await oneshot.invoker.handle_event({"event_type": "message_created"})
        assert "cleanup_all" not in oneshot.journal

        oneshot.adapter.release("work")
        assert (await accepted)["status"] == "done"
        await stopping
        assert oneshot.journal == [
            "r1: processing m1",
            "r1: turn work",
            "r1: room cleanup",
            "r1: turn work done",
            "r1: processed m1",
            "cleanup_all",
            "disconnect",
        ]
