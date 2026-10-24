"""Native OMP tool activity is reported independently of narration."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpTurn
from band.core.types import Emit
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.kit import EMIT_OFF, EMIT_ON, make_adapter, tool_end, tool_start


class TestOmpToolActivity:
    @pytest.mark.parametrize(
        "emit", [EMIT_ON, EMIT_OFF], ids=["narration-on", "narration-off"]
    )
    async def test_tool_activity_follows_the_narration_switch(
        self, tmp_path: Path, emit: tuple[Emit, ...]
    ) -> None:
        adapter = make_adapter(tmp_path, emit)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(
            tool_start("call-1", "Bash", command="private-command"), turn
        )
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)

        if emit:
            recorder.assert_completed_tool("Bash")
        else:
            assert recorder.events == []
        assert bool(tools.events_sent) is bool(emit)

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
        assert recorder.events == [{"kind": "tool_call", "name": "Write"}]

    @pytest.mark.parametrize(
        ("finished_id", "finished_name", "remaining_id", "remaining_name"),
        [
            ("call-a", "Read", "call-b", "Bash"),
            ("call-b", "Bash", "call-a", "Read"),
        ],
        ids=["older-finishes-first", "newer-finishes-first"],
    )
    async def test_finishing_one_tool_keeps_the_remaining_tool_visible(
        self,
        tmp_path: Path,
        finished_id: str,
        finished_name: str,
        remaining_id: str,
        remaining_name: str,
    ) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(tool_start("call-a", "Read"), turn)
        await adapter._emit_event(tool_start("call-b", "Bash"), turn)
        await adapter._emit_event(tool_end(finished_id, finished_name), turn)
        assert recorder.events[-1] == {"kind": "tool_call", "name": remaining_name}
        await adapter._emit_event(tool_end(remaining_id, remaining_name), turn)

        assert recorder.events[-1] == {"kind": "reasoning"}

    async def test_end_of_untracked_call_changes_nothing(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(tool_end("never-started", "Ghost"), turn)
        assert recorder.events == []

        await adapter._emit_event(tool_start("call-1", "Bash"), turn)
        await adapter._emit_event(tool_end("unknown", "Ghost"), turn)
        assert recorder.events == [{"kind": "tool_call", "name": "Bash"}]
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)
        recorder.assert_completed_tool("Bash")
