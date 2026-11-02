"""Own the permanent native subscription and its FIFO consumer."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio

from band.integrations.copilot_sdk.activity import SessionActivity
from tests.adapters.copilot_sdk.fakes import FakeCopilotSession


@pytest_asyncio.fixture(loop_scope="function")
async def activity_session() -> AsyncIterator[
    tuple[FakeCopilotSession, SessionActivity]
]:
    session = FakeCopilotSession("native", {}, reply_content=None)
    activity = SessionActivity(session)
    try:
        yield session, activity
    finally:
        await activity.close()
