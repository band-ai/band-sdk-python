"""Only prompt-attributed native work selects activity without narration."""

from __future__ import annotations

import pytest

from band.integrations.opencode import OpencodeToolStatus
from band.runtime.tools import BandTool
from band.testing import events_of_type
from band.testing.activity import SHOWN_REASONING, shown_tool
from tests.adapters.opencode.activity.kit import (
    MESSAGE,
    NATIVE_TOOL,
    SESSION,
    OpenActivityRoom,
    native_part,
)
from tests.adapters.opencode.helpers import event_message_updated

CURRENT_MESSAGE = "current-message"
CURRENT_CALL = "current-call"


class TestNativeActivity:
    async def test_matching_prompt_reports_running_and_terminal_without_narration(
        self, activity_room: OpenActivityRoom
    ) -> None:
        async with activity_room() as room:
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id),
                native_part(OpencodeToolStatus.RUNNING),
                native_part(OpencodeToolStatus.RUNNING),
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
            await room.publish(native_part(OpencodeToolStatus.COMPLETED))
            assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]
        assert events_of_type(room.tools, "tool_call") == []
        assert events_of_type(room.tools, "tool_result") == []

    @pytest.mark.parametrize(
        "parent_id", [None, "unrelated-prompt"], ids=["unknown", "wrong"]
    )
    async def test_unrelated_message_cannot_borrow_the_current_turns_activity(
        self, activity_room: OpenActivityRoom, parent_id: str | None
    ) -> None:
        async with activity_room() as room:
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=parent_id),
                native_part(OpencodeToolStatus.RUNNING),
            )
            assert room.recorder.events == []
            await room.publish(
                event_message_updated(
                    SESSION, CURRENT_MESSAGE, parent_id=room.prompt_id
                ),
                native_part(
                    OpencodeToolStatus.RUNNING,
                    call_id=CURRENT_CALL,
                    message_id=CURRENT_MESSAGE,
                ),
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
        assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]

    async def test_part_before_metadata_is_reported_once_its_parent_is_proven(
        self, activity_room: OpenActivityRoom
    ) -> None:
        async with activity_room() as room:
            await room.publish(native_part(OpencodeToolStatus.RUNNING))
            assert room.recorder.events == []
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id)
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
            await room.publish(native_part(OpencodeToolStatus.COMPLETED))
        assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]

    async def test_terminal_before_running_cannot_reopen_an_old_operation(
        self, activity_room: OpenActivityRoom
    ) -> None:
        async with activity_room() as room:
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id),
                native_part(OpencodeToolStatus.COMPLETED),
                native_part(OpencodeToolStatus.RUNNING),
            )
            assert room.recorder.events == []
            await room.publish(
                native_part(OpencodeToolStatus.RUNNING, call_id=CURRENT_CALL)
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
        assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]

    async def test_deferred_terminal_cannot_be_replaced_by_a_late_running_part(
        self, activity_room: OpenActivityRoom
    ) -> None:
        async with activity_room() as room:
            await room.publish(
                native_part(OpencodeToolStatus.COMPLETED),
                native_part(OpencodeToolStatus.RUNNING),
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id),
            )
            assert room.recorder.events == []
            await room.publish(
                native_part(OpencodeToolStatus.RUNNING, call_id=CURRENT_CALL)
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
        assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]

    async def test_wrapped_band_tool_events_do_not_duplicate_actual_execution(
        self, activity_room: OpenActivityRoom
    ) -> None:
        async with activity_room() as room:
            wrapped = room.registered_tool_name(BandTool.SEND_MESSAGE)
            await room.publish(
                event_message_updated(SESSION, MESSAGE, parent_id=room.prompt_id),
                native_part(OpencodeToolStatus.RUNNING, name=wrapped),
                native_part(OpencodeToolStatus.COMPLETED, name=wrapped),
            )
            assert room.recorder.events == []
            await room.publish(
                native_part(OpencodeToolStatus.RUNNING, call_id=CURRENT_CALL)
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
        assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]

    @pytest.mark.parametrize(
        ("summary", "agent"),
        [(True, None), (False, "compaction")],
        ids=["summary", "compaction-agent"],
    )
    async def test_compaction_continuation_is_not_attributed_as_native_foreground(
        self, activity_room: OpenActivityRoom, summary: bool, agent: str | None
    ) -> None:
        async with activity_room() as room:
            await room.publish(
                event_message_updated(
                    SESSION,
                    MESSAGE,
                    parent_id=room.prompt_id,
                    summary=summary,
                    agent=agent,
                ),
                native_part(OpencodeToolStatus.RUNNING),
            )
            assert room.recorder.events == []
            await room.publish(
                event_message_updated(
                    SESSION, CURRENT_MESSAGE, parent_id=room.prompt_id
                ),
                native_part(
                    OpencodeToolStatus.RUNNING,
                    call_id=CURRENT_CALL,
                    message_id=CURRENT_MESSAGE,
                ),
            )
            assert room.recorder.events == [shown_tool(NATIVE_TOOL)]
        assert room.recorder.events == [shown_tool(NATIVE_TOOL), SHOWN_REASONING]
