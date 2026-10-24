"""Native OMP permission waits are reported as activity whatever the narration choice."""

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
from band.integrations.ompwire import OmpUIRequest
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.kit import (
    EMIT_OFF,
    EMIT_ON,
    GatedPermissionResolver,
    make_adapter,
    permission_request,
)


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
        turn = OmpTurn(tools=tools)
        release = asyncio.Event()
        entered = asyncio.Event()
        waiting_during: list[bool] = []

        async def resolve(request: OmpPermissionRequest) -> str | None:
            waiting_during.append(waiting(recorder))
            entered.set()
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
            adapter._permission(request, "room", "sess", turn)
        )
        try:
            await entered.wait()
            assert waiting(recorder)
            release.set()
            response = await running
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

        assert response.value == "Approve"
        assert waiting_during == [True]
        assert not waiting(recorder)
        assert recorder.events == [{"waiting": True}, {"waiting": False}]
        assert not tools.events_sent

    @pytest.mark.parametrize("outcome", ["complete", "cancel", "error"])
    async def test_concurrent_permissions_keep_waiting_until_the_last_resolver_finishes(
        self, tmp_path: Path, outcome: str
    ) -> None:
        recorder = ActivityRecorder()
        turn = OmpTurn(tools=recorder.observing(FakeAgentTools()))
        resolver = GatedPermissionResolver("first", "second")
        adapter = make_adapter(tmp_path, EMIT_OFF, resolve_permission=resolver)
        first = asyncio.create_task(
            adapter._permission(permission_request("first"), "room", "sess", turn)
        )
        second = asyncio.create_task(
            adapter._permission(permission_request("second"), "room", "sess", turn)
        )
        try:
            await resolver.entered["first"].wait()
            await resolver.entered["second"].wait()

            match outcome:
                case "complete":
                    resolver.responses["first"].set_result("Approve")
                    await first
                case "cancel":
                    first.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await first
                case "error":
                    resolver.responses["first"].set_exception(
                        RuntimeError("permission resolver failed")
                    )
                    with pytest.raises(
                        RuntimeError, match="permission resolver failed"
                    ):
                        await first

            assert waiting(recorder)
            assert not second.done()
            resolver.responses["second"].set_result("Approve")
            await second
            assert not waiting(recorder)
        finally:
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    @pytest.mark.parametrize(
        "second_room",
        ["other-room", "room"],
        ids=["independent-room", "replacement-turn"],
    )
    async def test_permission_waits_belong_to_the_turn_that_started_them(
        self, tmp_path: Path, second_room: str
    ) -> None:
        first_recorder, second_recorder = ActivityRecorder(), ActivityRecorder()
        first_turn = OmpTurn(tools=first_recorder.observing(FakeAgentTools()))
        second_turn = OmpTurn(tools=second_recorder.observing(FakeAgentTools()))
        resolver = GatedPermissionResolver("first", "second")
        adapter = make_adapter(tmp_path, EMIT_OFF, resolve_permission=resolver)
        first = asyncio.create_task(
            adapter._permission(permission_request("first"), "room", "sess", first_turn)
        )
        second = asyncio.create_task(
            adapter._permission(
                permission_request("second"), second_room, "sess", second_turn
            )
        )
        try:
            await resolver.entered["first"].wait()
            await resolver.entered["second"].wait()
            resolver.responses["first"].set_result("Approve")
            await first

            assert not waiting(first_recorder)
            assert waiting(second_recorder)
            resolver.responses["second"].set_result("Approve")
            await second
            assert not waiting(second_recorder)
        finally:
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_auto_approved_selection_reports_no_wait(
        self, tmp_path: Path
    ) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)
        request = OmpUIRequest(
            id="ui-1",
            method="select",
            title="Allow tool: Bash",
            options=["Approve", "Deny"],
        )

        response = await adapter._permission(request, "room", "sess", turn)

        assert response.value == "Approve"
        assert recorder.events == []

    async def test_non_select_ui_requests_report_no_wait(self, tmp_path: Path) -> None:
        adapter = make_adapter(tmp_path, EMIT_OFF)
        recorder = ActivityRecorder()
        tools = recorder.observing(FakeAgentTools())
        turn = OmpTurn(tools=tools)

        response = await adapter._permission(
            OmpUIRequest(id="ui-1", method="confirm", title="private-question"),
            "room",
            "sess",
            turn,
        )

        assert response.cancelled
        assert recorder.events == []
