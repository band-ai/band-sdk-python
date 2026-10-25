"""Host tools preserve authorization, reply and error outcomes."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.ompwire import OmpEvent, OmpEventType
from band.testing import MISSING_REPLY_FAILURE, failure_reports
from tests.adapters.omp.activitykit import tool_end
from tests.adapters.omp.failures import FailedDeliveryTools
from tests.adapters.omp.turns import NativeTurnFactory, reply_call


class TestToolBridging:
    async def test_excluded_host_tool_cannot_complete_turn(
        self, native_turn: NativeTurnFactory
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(
                exclude_tools=["band_send_message"], emit=()
            ) as turn:
                result = await turn.callbacks.tool(reply_call("Not authorized"))
                assert result.is_error
        assert not turn.tools.turn.replied
        assert not turn.tools.tool_calls

    async def test_failed_band_reply_remains_an_error_for_the_model(
        self, native_turn: NativeTurnFactory
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(tools=FailedDeliveryTools(), emit=()) as turn:
                result = await turn.callbacks.tool(reply_call("Answer"))
                assert result.is_error
                assert result.content[0].text == "delivery rejected"
        assert not turn.tools.turn.replied

    async def test_searching_external_reply_tool_docs_does_not_reply(
        self, native_turn: NativeTurnFactory, tmp_path: Path
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(
                config=OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=False),
                emit=(),
            ) as turn:
                await turn.callbacks.event(
                    OmpEvent(
                        type=OmpEventType.TOOL_START,
                        toolCallId="docs",
                        toolName="grep",
                        args={
                            "path": "xd://mcp__band_sdk_band_band_send_message",
                            "pattern": ".",
                        },
                    )
                )
                await turn.callbacks.event(tool_end("docs", "grep"))
        assert not turn.tools.turn.replied
        assert failure_reports(turn.tools) == [MISSING_REPLY_FAILURE]
