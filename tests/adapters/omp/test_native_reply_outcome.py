"""External native replies satisfy the judged turn only after tool success."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OMP_TOOL_APPROVAL_HEADER
from band.core.protocols import TurnResultAlreadyReported
from band.runtime.tools.types import BandTool
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.omp.kit import owned_rpc_turn

NATIVE_REPLY_TOOL = f"mcp__{BandTool.SEND_MESSAGE}"
REPLY_PERMISSION_TITLE = f"{OMP_TOOL_APPROVAL_HEADER}{NATIVE_REPLY_TOOL}"
REPLY_ARGUMENTS = {"content": "Native reply"}


class TestNativeReplyOutcome:
    async def test_successful_native_reply_completes_the_judged_turn(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        tools = FakeAgentTools()
        await owned_rpc_turn(
            monkeypatch,
            tmp_path,
            REPLY_PERMISSION_TITLE,
            "permission-first",
            native_name=NATIVE_REPLY_TOOL,
            native_arguments=REPLY_ARGUMENTS,
            native_error=False,
            inject_band_tools=False,
            tools=tools,
            judged=True,
        )

        assert tools.turn.replied
        assert failure_reports(tools) == []

    async def test_failed_native_reply_reports_the_missing_reply(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        tools = FakeAgentTools()
        with pytest.raises(TurnResultAlreadyReported):
            await owned_rpc_turn(
                monkeypatch,
                tmp_path,
                REPLY_PERMISSION_TITLE,
                "permission-first",
                native_name=NATIVE_REPLY_TOOL,
                native_arguments=REPLY_ARGUMENTS,
                native_error=True,
                inject_band_tools=False,
                tools=tools,
                judged=True,
            )

        assert not tools.turn.replied
        assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
