from __future__ import annotations

import pytest
import pytest_asyncio

from band.integrations.claude_sdk.activity import Origin
from band.testing import FakeAgentTools
from band.testing.activity import ActivityRecorder
from tests.adapters.claude_sdk.activity.kit import (
    BACKGROUND_CALL,
    BACKGROUND_TASK,
    native_task_started,
    native_tool_result,
    native_tool_uses,
)
from tests.adapters.claude_sdk.helpers import ClaudeRoom, OpenRoom


@pytest.fixture
def recorder() -> ActivityRecorder:
    return ActivityRecorder()


@pytest_asyncio.fixture(loop_scope="function")
async def background_origin(
    claude_room: OpenRoom,
) -> tuple[ClaudeRoom, FakeAgentTools, ActivityRecorder, Origin]:
    room = await claude_room(emit=())
    recorder = ActivityRecorder()
    original = recorder.observing(room.fresh_tools())
    room.claude.script(
        [
            room.model_reply("Started."),
            native_tool_uses((BACKGROUND_CALL, "Bash")),
            native_task_started(BACKGROUND_TASK, BACKGROUND_CALL),
            native_tool_result(BACKGROUND_CALL),
        ]
    )
    await room.send("Start background work", tools=original)
    session = room.session_manager.session(room.room_id)
    assert session is not None and session.activity is not None
    return room, original, recorder, session.activity.resolve(BACKGROUND_CALL)
