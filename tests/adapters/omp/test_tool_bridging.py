"""Native OMP tool execution preserves reply and error outcomes."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.ompwire import OmpHostToolCall
from band.testing import MISSING_REPLY_FAILURE, FakeAgentTools, failure_reports
from tests.adapters.omp.kit import (
    DocumentationRuntime,
    FailedDeliveryTools,
    HostToolCallRuntime,
    run_turn,
)


class TestToolBridging:
    @pytest.mark.asyncio
    async def test_excluded_host_tool_cannot_complete_turn(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = HostToolCallRuntime(
            OmpHostToolCall(
                id="call",
                toolCallId="reply",
                toolName="band_send_message",
                arguments={"content": "Not authorized"},
            )
        )
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            exclude_tools=["band_send_message"],
            emit=(),
        )
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert runtime.tool_result is not None
            assert runtime.tool_result.is_error
            assert not tools.turn.replied
            assert not tools.tool_calls
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_failed_band_reply_remains_an_error_for_the_model(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = HostToolCallRuntime(
            OmpHostToolCall(
                id="call",
                toolCallId="reply",
                toolName="band_send_message",
                arguments={"content": "Answer"},
            )
        )
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit=())
        tools = FailedDeliveryTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert runtime.tool_result is not None
            assert runtime.tool_result.is_error
            assert runtime.tool_result.content[0].text == "delivery rejected"
            assert not tools.turn.replied
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_searching_external_reply_tool_docs_does_not_reply(
        self, monkeypatch, tmp_path: Path
    ):
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", DocumentationRuntime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=False), emit=()
        )
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert not tools.turn.replied
            assert failure_reports(tools) == [MISSING_REPLY_FAILURE]
        finally:
            await adapter.cleanup_all()
