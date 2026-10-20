"""Native OMP activity reporting: tool and permission hooks, independent of narration."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import (
    OmpAdapter,
    OmpAdapterConfig,
    OmpPermissionRequest,
    OmpTurn,
)
from band.core.types import Emit
from band.integrations.ompwire import (
    OmpContent,
    OmpEvent,
    OmpToolResult,
    OmpUIRequest,
)
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools

EMIT_ON: tuple[Emit, ...] = (Emit.TOOL_CALLS,)
EMIT_OFF: tuple[Emit, ...] = ()


def make_adapter(
    tmp_path: Path,
    emit: tuple[Emit, ...],
    resolve_permission=None,  # type: ignore[no-untyped-def]
) -> OmpAdapter:
    return OmpAdapter(
        OmpAdapterConfig(cwd=str(tmp_path)),
        emit=emit,
        resolve_permission=resolve_permission,
    )


def tool_start(call_id: str, name: str, **args: object) -> OmpEvent:
    return OmpEvent(
        type="tool_execution_start",
        toolCallId=call_id,
        toolName=name,
        args=args,
    )


def tool_end(call_id: str, name: str, *, is_error: bool = False) -> OmpEvent:
    return OmpEvent(
        type="tool_execution_end",
        toolCallId=call_id,
        toolName=name,
        result=OmpToolResult(content=[OmpContent(type="text", text="private-result")]),
        isError=is_error,
    )


class TestOmpToolActivity:
    @pytest.mark.parametrize(
        "emit", [EMIT_ON, EMIT_OFF], ids=["narration-on", "narration-off"]
    )
    async def test_start_and_end_are_independent_of_narration(
        self, tmp_path: Path, emit: tuple[Emit, ...]
    ) -> None:
        adapter = make_adapter(tmp_path, emit)
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(
            tool_start("call-1", "Bash", command="private-command"), turn
        )
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)

        recorder.assert_completed_tool("Bash")
        assert bool(tools.events_sent) is bool(emit)

    async def test_only_the_name_reaches_the_recorder(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_ON)
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(
            tool_start(
                "call-1", "Write", file_path="private-path", content="private-content"
            ),
            turn,
        )
        assert recorder.events == [{"kind": "tool_call", "name": "Write"}]

    async def test_latest_open_tool_wins(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(tool_start("call-a", "Read"), turn)
        await adapter._emit_event(tool_start("call-b", "Bash"), turn)
        assert recorder.events == [
            {"kind": "tool_call", "name": "Read"},
            {"kind": "tool_call", "name": "Bash"},
        ]

    async def test_end_of_untracked_call_changes_nothing(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        turn = OmpTurn(tools=tools)

        await adapter._emit_event(tool_end("never-started", "Ghost"), turn)
        assert recorder.events == []

        await adapter._emit_event(tool_start("call-1", "Bash"), turn)
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)
        await adapter._emit_event(tool_end("call-1", "Bash"), turn)
        recorder.assert_completed_tool("Bash")


class TestOmpPermissionActivity:
    @pytest.mark.parametrize(
        "emit", [EMIT_ON, EMIT_OFF], ids=["narration-on", "narration-off"]
    )
    async def test_wait_is_reported_whatever_the_narration_choice(
        self, tmp_path: Path, emit: tuple[Emit, ...]
    ) -> None:
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        release = asyncio.Event()
        waiting_during: list[bool] = []

        async def resolve(request: OmpPermissionRequest) -> str | None:
            waiting_during.append(recorder.waiting)
            await release.wait()
            return "Approve" if request.options == ("Approve", "Deny") else None

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            emit=emit,
            resolve_permission=resolve,
        )
        request = OmpUIRequest(
            id="ui-1",
            method="select",
            title='Allow tool: Bash\nArguments: {"command": "private-command"}',
            options=["Approve", "Deny"],
        )
        running = asyncio.create_task(
            adapter._permission(request, "room", "sess", tools)
        )
        while not waiting_during:
            await asyncio.sleep(0)
        assert recorder.waiting
        release.set()
        response = await running

        assert response.value == "Approve"
        assert waiting_during == [True]
        assert not recorder.waiting
        assert recorder.events == [{"waiting": True}, {"waiting": False}]
        assert not tools.events_sent

    async def test_auto_approved_selection_reports_no_wait(
        self, tmp_path: Path
    ) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder
        request = OmpUIRequest(
            id="ui-1",
            method="select",
            title="Allow tool: Bash",
            options=["Approve", "Deny"],
        )

        response = await adapter._permission(request, "room", "sess", tools)

        assert response.value == "Approve"
        assert recorder.events == []

    async def test_non_select_ui_requests_report_no_wait(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        tools = FakeAgentTools()
        recorder = ActivityRecorder()
        tools._activity_observer = recorder

        response = await adapter._permission(
            OmpUIRequest(id="ui-1", method="confirm", title="private-question"),
            "room",
            "sess",
            tools,
        )

        assert response.cancelled
        assert recorder.events == []
