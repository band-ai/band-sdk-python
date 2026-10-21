"""Native OMP permission waits are reported as activity whatever the narration choice."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig, OmpPermissionRequest
from band.core.types import Emit
from band.integrations.ompwire import OmpUIRequest
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.kit import EMIT_OFF, EMIT_ON, make_adapter


def waiting(recorder: ActivityRecorder) -> bool:
    """Whether the last manual-wait observation left the turn waiting."""
    waits = [event["waiting"] for event in recorder.events if "waiting" in event]
    return bool(waits) and waits[-1]


class TestOmpPermissionActivity:
    @pytest.mark.parametrize(
        "emit", [EMIT_ON, EMIT_OFF], ids=["narration-on", "narration-off"]
    )
    async def test_wait_is_reported_whatever_the_narration_choice(
        self, tmp_path: Path, emit: tuple[Emit, ...]
    ) -> None:
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        release = asyncio.Event()
        waiting_during: list[bool] = []

        async def resolve(request: OmpPermissionRequest) -> str | None:
            waiting_during.append(waiting(recorder))
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
        assert waiting(recorder)
        release.set()
        response = await running

        assert response.value == "Approve"
        assert waiting_during == [True]
        assert not waiting(recorder)
        assert recorder.events == [{"waiting": True}, {"waiting": False}]
        assert not tools.events_sent

    async def test_auto_approved_selection_reports_no_wait(
        self, tmp_path: Path
    ) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
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
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())

        response = await adapter._permission(
            OmpUIRequest(id="ui-1", method="confirm", title="private-question"),
            "room",
            "sess",
            tools,
        )

        assert response.cancelled
        assert recorder.events == []
