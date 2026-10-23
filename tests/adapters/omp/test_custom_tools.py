"""Caller-supplied host tools are advertised and dispatched natively."""

from __future__ import annotations

import pytest

from band.adapters.omp import OmpAdapter
from band.integrations.ompwire import OmpHostToolCall
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import EchoInput


class TestCustomTools:
    @pytest.mark.asyncio
    async def test_additional_tool_is_advertised_and_dispatched(self) -> None:
        observed: list[str] = []

        def echo(inp: EchoInput) -> str:
            observed.append(inp.text)
            return f"echoed:{inp.text}"

        adapter = OmpAdapter(additional_tools=[(EchoInput, echo)], emit=())
        result = await adapter._execute_tool(
            OmpHostToolCall(
                id="call",
                toolCallId="echo",
                toolName="echo",
                arguments={"text": "ping"},
            ),
            FakeAgentTools(),
        )

        assert any(tool["name"] == "echo" for tool in adapter._host_tools())
        assert observed == ["ping"]
        assert result.content[0].text == "echoed:ping"
        assert not result.is_error
