"""Native events own activity across injected host-tool callbacks."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from band.core.types import Emit
from band.integrations.omp import OMP_FORM_APPROVE
from band.integrations.ompwire import OmpHostToolCall
from band.runtime.activity import PERMISSION, REASONING, tool_call_activity
from band.runtime.tools.types import BandTool
from band.testing.activity import SHOWN_REASONING, shown_tool
from tests.adapters.omp.activitykit import (
    EMIT_OFF,
    EMIT_ON,
    GatedPermissionResolver,
    activity_turn,
    permission_request,
    tool_end,
    tool_start,
)


@pytest.mark.usefixtures("native_startup_ready")
class TestActivityOwnership:
    @pytest.mark.parametrize(
        "emit", [EMIT_ON, EMIT_OFF], ids=["narration-on", "narration-off"]
    )
    async def test_host_callback_does_not_finish_native_activity(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mock_rest_client: MagicMock,
        emit: tuple[Emit, ...],
    ) -> None:
        async with activity_turn(
            monkeypatch, tmp_path, mock_rest_client, emit=emit
        ) as turn:
            await turn.runtime.on_event(tool_start("native", "read"))
            await turn.runtime.on_event(tool_start("host", BandTool.LOOKUP_PEERS))
            result = await turn.runtime.on_tool(
                OmpHostToolCall(
                    id="host-request",
                    toolCallId="host",
                    toolName=BandTool.LOOKUP_PEERS,
                    arguments={},
                )
            )
            assert not result.is_error
            assert turn.recorder.activity.detail == tool_call_activity(
                BandTool.LOOKUP_PEERS
            )

            await turn.runtime.on_event(tool_end("host", BandTool.LOOKUP_PEERS))
            assert turn.recorder.activity.detail == tool_call_activity("read")
            await turn.runtime.on_event(tool_end("native", "read"))
            await turn.finish()

            assert turn.recorder.events == [
                shown_tool("read"),
                shown_tool(BandTool.LOOKUP_PEERS),
                shown_tool("read"),
                SHOWN_REASONING,
            ]
            assert turn.recorder.activity.detail == REASONING

    async def test_permission_wait_overrides_tool_progress_then_restores_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mock_rest_client: MagicMock,
    ) -> None:
        resolver = GatedPermissionResolver("permission")
        async with activity_turn(
            monkeypatch, tmp_path, mock_rest_client, resolve_permission=resolver
        ) as turn:
            await turn.runtime.on_event(tool_start("older", "read"))
            permission = asyncio.create_task(
                turn.runtime.on_ui(permission_request("permission"))
            )
            try:
                await resolver.entered["permission"].wait()
                assert turn.recorder.activity.detail == PERMISSION
                await turn.runtime.on_event(tool_start("newer", "grep"))
                await turn.runtime.on_event(tool_end("older", "read"))
                assert turn.recorder.activity.detail == PERMISSION

                resolver.responses["permission"].set_result(OMP_FORM_APPROVE)
                response = await permission
                assert response.value == OMP_FORM_APPROVE
                assert turn.recorder.activity.detail == tool_call_activity("grep")

                await turn.runtime.on_event(tool_end("newer", "grep"))
                await turn.finish()
                assert turn.recorder.activity.detail == REASONING
            finally:
                permission.cancel()
                await asyncio.gather(permission, return_exceptions=True)
