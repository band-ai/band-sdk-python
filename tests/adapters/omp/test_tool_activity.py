"""Native OMP tool activity is independent of narration and follows call ownership."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp.turnstate import OmpTurn
from band.core.types import Emit
from band.runtime.activity import REASONING, tool_call_activity
from band.testing.activity import SHOWN_REASONING, ActivityRecorder, shown_tool
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.activitykit import (
    EMIT_OFF,
    EMIT_ON,
    make_adapter,
    tool_end,
    tool_start,
)


class TestOmpToolActivity:
    @pytest.mark.parametrize(
        ("emit", "narrated"),
        [(EMIT_ON, True), (EMIT_OFF, False)],
        ids=["narration-on", "narration-off"],
    )
    async def test_tool_activity_is_reported_whether_or_not_it_is_narrated(
        self,
        tmp_path: Path,
        emit: tuple[Emit, ...],
        narrated: bool,
    ) -> None:
        adapter = make_adapter(tmp_path, emit)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(
            tool_start("call-1", "Bash", command="private-command"), turn
        )
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)

        assert recorder.events == [shown_tool("Bash"), SHOWN_REASONING]
        assert bool(tools.events_sent) is narrated

    async def test_only_the_name_reaches_the_recorder(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(
            tool_start(
                "call-1", "Write", file_path="private-path", content="private-content"
            ),
            turn,
        )
        assert recorder.events == [shown_tool("Write")]

    @pytest.mark.parametrize(
        ("first_name", "second_name"),
        [
            ("Read", "Bash"),
            ("Bash", "Bash"),
        ],
        ids=["different-tools", "same-tool"],
    )
    @pytest.mark.parametrize(
        ("finished_id", "remaining_id"),
        [("call-a", "call-b"), ("call-b", "call-a")],
        ids=["older-finishes-first", "newer-finishes-first"],
    )
    async def test_finishing_one_tool_keeps_the_remaining_tool_visible(
        self,
        tmp_path: Path,
        first_name: str,
        second_name: str,
        finished_id: str,
        remaining_id: str,
    ) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)
        names = {"call-a": first_name, "call-b": second_name}

        await adapter._emit_event(tool_start("call-a", first_name), turn)
        await adapter._emit_event(tool_start("call-b", second_name), turn)
        await adapter._emit_event(tool_end(finished_id, names[finished_id]), turn)
        assert recorder.activity.progress == tool_call_activity(names[remaining_id])
        await adapter._emit_event(tool_end(remaining_id, names[remaining_id]), turn)

        assert recorder.activity.progress == REASONING

    async def test_finishing_newest_tool_restores_the_newest_remaining_call(
        self, tmp_path: Path
    ) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        recorder = ActivityRecorder()
        turn = OmpTurn(tools=recorder.observing(FakeAgentTools()))

        await adapter._emit_event(tool_start("oldest", "Read"), turn)
        await adapter._emit_event(tool_start("middle", "Bash"), turn)
        await adapter._emit_event(tool_start("newest", "Write"), turn)

        await adapter._emit_event(tool_end("newest", "Write"), turn)
        await adapter._emit_event(tool_end("middle", "Bash"), turn)
        await adapter._emit_event(tool_end("oldest", "Read"), turn)

        assert recorder.events == [
            shown_tool("Read"),
            shown_tool("Bash"),
            shown_tool("Write"),
            shown_tool("Bash"),
            shown_tool("Read"),
            SHOWN_REASONING,
        ]

    async def test_end_of_untracked_call_changes_nothing(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(tool_end("never-started", "Ghost"), turn)
        assert recorder.events == []

        await adapter._emit_event(tool_start("call-1", "Bash"), turn)
        await adapter._emit_event(tool_end("unknown", "Ghost"), turn)
        assert recorder.events == [shown_tool("Bash")]
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)
        recorder.assert_completed_tool("Bash")
