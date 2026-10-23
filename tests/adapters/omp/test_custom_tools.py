"""Caller-supplied host tools are advertised and dispatched natively."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    CustomHostToolRuntime,
    EchoDetail,
    EchoInput,
    EchoResult,
    requests_named,
    run_turn,
)


class TestCustomTools:
    @pytest.mark.asyncio
    async def test_rejected_arguments_keep_private_input_out_of_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        private_value = "private-custom-input-6a91"

        def echo(inp: EchoInput) -> str:
            pytest.fail("Rejected arguments must not invoke the custom handler")

        runtime = CustomHostToolRuntime(
            arguments={"text": {"credential": private_value}}
        )
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            additional_tools=[(EchoInput, echo)],
            emit=(),
        )
        tools = FakeAgentTools()
        try:
            with caplog.at_level(logging.WARNING, logger="band.adapters.omp"):
                await run_turn(adapter, tools)
            assert runtime.tool_result is not None
            assert runtime.tool_result.is_error
            assert "text:" in runtime.tool_result.content[0].text
            assert private_value not in caplog.text
            assert tools.turn.replied
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_additional_tool_is_advertised_and_dispatched(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        observed: list[str] = []

        def echo(inp: EchoInput) -> str:
            observed.append(inp.text)
            return f"echoed:{inp.text}"

        runtime = CustomHostToolRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            additional_tools=[(EchoInput, echo)],
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
        finally:
            await adapter.cleanup_all()

        advertised = requests_named(runtime, OmpCommandName.SET_HOST_TOOLS)
        assert advertised
        assert any(tool["name"] == "echo" for tool in advertised[0]["tools"])
        assert observed == ["ping"]
        assert runtime.tool_result is not None
        assert runtime.tool_result.content[0].text == "echoed:ping"
        assert not runtime.tool_result.is_error

    @pytest.mark.asyncio
    async def test_nested_model_and_bytes_use_structured_result_encoding(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def echo(inp: EchoInput) -> EchoResult:
            return EchoResult(detail=EchoDetail(text=inp.text, payload=b"\xff\x00"))

        runtime = CustomHostToolRuntime()
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            additional_tools=[(EchoInput, echo)],
            emit=(),
        )
        try:
            await run_turn(adapter, FakeAgentTools())
        finally:
            await adapter.cleanup_all()

        assert runtime.tool_result is not None
        assert not runtime.tool_result.is_error
        assert json.loads(runtime.tool_result.content[0].text) == {
            "detail": {"text": "ping", "payload": "_wA="}
        }
