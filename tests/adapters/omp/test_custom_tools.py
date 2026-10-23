"""Caller-supplied host tools are advertised and dispatched natively."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    CustomHostToolRuntime,
    EchoInput,
    requests_named,
    run_turn,
)


class TestCustomTools:
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
