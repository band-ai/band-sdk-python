"""A manual permission lease spans the actual request task's response I/O."""

from __future__ import annotations

import asyncio

from band.runtime.activity import retain_request_permission
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder


class TestRequestLifetime:
    async def test_permission_remains_until_the_request_has_written_its_response(
        self,
    ) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        recorder.observing(tools)
        answer, writing, release = (asyncio.Event() for _ in range(3))

        async def request() -> None:
            retain_request_permission(tools)
            await answer.wait()
            writing.set()
            await release.wait()

        running = asyncio.create_task(request())
        try:
            answer.set()
            await writing.wait()
            assert recorder.waiting
        finally:
            release.set()
            await running
        assert not recorder.waiting
