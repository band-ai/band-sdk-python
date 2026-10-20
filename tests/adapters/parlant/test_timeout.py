"""Response timeout stops processing before the Band turn returns."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from band.adapters.parlant import ParlantAdapter
from band.adapters.parlant.messages import process_session_tag
from tests.adapters.parlant.helpers import SESSION_ID, RecordingBackgroundTasks

pytestmark = [pytest.mark.usefixtures("parlant_sessions"), pytest.mark.looptime]


class TestParlantTimeout:
    async def test_timeout_cancels_and_joins_processing(
        self,
        start_adapter: Callable[..., Awaitable[ParlantAdapter]],
        run_turn: Callable[..., Awaitable[None]],
        background_tasks: RecordingBackgroundTasks,
        processing_session: asyncio.Task[None],
    ) -> None:
        adapter = await start_adapter()

        await run_turn(adapter)

        assert background_tasks.cancelled == [process_session_tag(SESSION_ID)]
        assert processing_session.done()
