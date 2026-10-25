"""Native OMP permission waits are reported as activity whatever the narration choice."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from band.adapters.omp.turnstate import OmpTurn
from band.core.types import Emit
from band.integrations.omp import OMP_FORM_APPROVE
from band.integrations.ompwire import OmpUIRequest
from band.testing.activity import ActivityRecorder
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.omp.activitykit import (
    EMIT_OFF,
    EMIT_ON,
    GatedPermissionResolver,
    concurrent_permissions,
    make_adapter,
    permission_request,
)

PERMISSION_FAILURE = "permission resolver failed"


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
        resolver = GatedPermissionResolver("ui-1")
        adapter = make_adapter(tmp_path, emit, resolve_permission=resolver)
        request = permission_request("ui-1")
        running = asyncio.create_task(
            adapter._permission(request, "room", "sess", turn)
        )
        try:
            await resolver.entered["ui-1"].wait()
            assert recorder.waiting
            resolver.responses["ui-1"].set_result(OMP_FORM_APPROVE)
            response = await running
        finally:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

        assert response.value == OMP_FORM_APPROVE
        assert not recorder.waiting
        assert recorder.events == [{"waiting": True}, {"waiting": False}]
        assert not tools.events_sent

    async def test_completed_permission_keeps_the_other_resolver_waiting(
        self, tmp_path: Path
    ) -> None:
        async with concurrent_permissions(tmp_path) as pending:
            pending.resolver.responses["first"].set_result(OMP_FORM_APPROVE)
            await pending.first

            assert pending.recorder.waiting
            assert not pending.second.done()
            pending.resolver.responses["second"].set_result(OMP_FORM_APPROVE)
            await pending.second
            assert not pending.recorder.waiting

    async def test_cancelled_permission_keeps_the_other_resolver_waiting(
        self, tmp_path: Path
    ) -> None:
        async with concurrent_permissions(tmp_path) as pending:
            pending.first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending.first

            assert pending.recorder.waiting
            assert not pending.second.done()
            pending.resolver.responses["second"].set_result(OMP_FORM_APPROVE)
            await pending.second
            assert not pending.recorder.waiting

    async def test_failed_permission_keeps_the_other_resolver_waiting(
        self, tmp_path: Path
    ) -> None:
        async with concurrent_permissions(tmp_path) as pending:
            pending.resolver.responses["first"].set_exception(
                RuntimeError(PERMISSION_FAILURE)
            )
            with pytest.raises(RuntimeError, match=PERMISSION_FAILURE):
                await pending.first

            assert pending.recorder.waiting
            assert not pending.second.done()
            pending.resolver.responses["second"].set_result(OMP_FORM_APPROVE)
            await pending.second
            assert not pending.recorder.waiting

    async def test_cancelling_the_final_permission_clears_the_wait(
        self, tmp_path: Path
    ) -> None:
        async with concurrent_permissions(tmp_path) as pending:
            pending.resolver.responses["first"].set_result(OMP_FORM_APPROVE)
            await pending.first
            assert pending.recorder.waiting

            pending.second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending.second

            assert not pending.recorder.waiting

    async def test_failing_the_final_permission_clears_the_wait(
        self, tmp_path: Path
    ) -> None:
        async with concurrent_permissions(tmp_path) as pending:
            pending.resolver.responses["first"].set_result(OMP_FORM_APPROVE)
            await pending.first
            assert pending.recorder.waiting

            pending.resolver.responses["second"].set_exception(
                RuntimeError(PERMISSION_FAILURE)
            )
            with pytest.raises(RuntimeError, match=PERMISSION_FAILURE):
                await pending.second

            assert not pending.recorder.waiting

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
            resolver.responses["first"].set_result(OMP_FORM_APPROVE)
            await first

            assert not first_recorder.waiting
            assert second_recorder.waiting
            resolver.responses["second"].set_result(OMP_FORM_APPROVE)
            await second
            assert not second_recorder.waiting
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
        request = permission_request("ui-1")

        response = await adapter._permission(request, "room", "sess", turn)

        assert response.value == OMP_FORM_APPROVE
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
