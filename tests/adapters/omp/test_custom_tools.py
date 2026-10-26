"""Custom tools are advertised, validated and encoded at the host boundary."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.integrations.ompwire import OmpCommandName, OmpHostToolCall
from band.testing import FakeAgentTools
from tests.adapters.omp.schemas import EchoDetail, EchoInput, EchoResult
from tests.adapters.omp.turns import NativeTurnFactory, reply_call
from tests.integrations.omprpc.wirelog import checkpoint_frames


class TestCustomTools:
    async def test_rejected_arguments_keep_private_input_out_of_logs(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        private_value = "private-custom-input-6a91"

        def echo(inp: EchoInput) -> str:
            pytest.fail("Rejected arguments must not invoke the custom handler")

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            additional_tools=[(EchoInput, echo)],
            emit=(),
        )
        with caplog.at_level(logging.WARNING, logger="band.adapters.omp"):
            result = await adapter._execute_tool(
                OmpHostToolCall(
                    id="call",
                    toolCallId="echo",
                    toolName="echo",
                    arguments={"text": {"credential": private_value}},
                ),
                FakeAgentTools(),
            )
        assert result.is_error
        assert "text:" in result.content[0].text
        assert private_value not in caplog.text

    async def test_additional_tool_is_advertised_and_dispatched(
        self, native_turn: NativeTurnFactory
    ) -> None:
        observed: list[str] = []

        def echo(inp: EchoInput) -> str:
            observed.append(inp.text)
            return f"echoed:{inp.text}"

        async with native_turn(additional_tools=[(EchoInput, echo)], emit=()) as turn:
            result = await turn.callbacks.tool(
                OmpHostToolCall(
                    id="call",
                    toolCallId="echo",
                    toolName="echo",
                    arguments={"text": "ping"},
                )
            )
            await turn.callbacks.tool(reply_call("Finished the custom tool"))
            advertised = [
                frame
                for frame in await checkpoint_frames(turn.client)
                if frame["type"] == OmpCommandName.SET_HOST_TOOLS
            ]
        assert any(tool["name"] == "echo" for tool in advertised[0]["tools"])
        assert observed == ["ping"]
        assert result.content[0].text == "echoed:ping"
        assert not result.is_error
        assert turn.tools.turn.replied

    async def test_nested_model_and_bytes_use_structured_result_encoding(
        self, tmp_path: Path
    ) -> None:
        def echo(inp: EchoInput) -> EchoResult:
            return EchoResult(detail=EchoDetail(text=inp.text, payload=b"\xff\x00"))

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            additional_tools=[(EchoInput, echo)],
            emit=(),
        )
        result = await adapter._execute_tool(
            OmpHostToolCall(
                id="call",
                toolCallId="echo",
                toolName="echo",
                arguments={"text": "ping"},
            ),
            FakeAgentTools(),
        )
        assert not result.is_error
        assert json.loads(result.content[0].text) == {
            "detail": {"text": "ping", "payload": "_wA="}
        }
