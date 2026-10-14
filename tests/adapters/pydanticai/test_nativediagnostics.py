"""Unsettled native runs expose bounded metadata without their content."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

import band.adapters.pydantic_ai as pydantic_adapter
from band.adapters.pydantic_ai import PydanticAIAdapter
from band.core.types import PlatformMessage
from band.runtime.tools import BandTool
from band.testing import FakeAgentTools


class TestNativeDiagnostics:
    @pytest.mark.parametrize(
        ("first_tool", "native_text", "expected_metadata"),
        [
            (
                None,
                "private native reply",
                "output_type=str, output_chars=20, tool_calls=0, tool_results=0, failed_tool_results=0",
            ),
            (
                None,
                "   ",
                "output_type=NoneType, output_chars=0, tool_calls=0, tool_results=0, failed_tool_results=0",
            ),
            (
                BandTool.SEND_MESSAGE,
                "private native reply",
                "output_type=str, output_chars=20, tool_calls=1, tool_results=1, failed_tool_results=1",
            ),
            (BandTool.NO_REPLY, "private native reply", None),
        ],
    )
    async def test_native_outcomes_are_distinguishable_without_logging_content(
        self,
        sample_message: PlatformMessage,
        caplog: pytest.LogCaptureFixture,
        first_tool: BandTool | None,
        native_text: str,
        expected_metadata: str | None,
    ) -> None:
        requests = 0

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            nonlocal requests
            requests += 1
            if first_tool is not None and requests == 1:
                yield {0: DeltaToolCall(name=first_tool, json_args="{}")}
            else:
                yield native_text

        adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=stream))
        await adapter.on_started("Bot", "Bot")
        tools = FakeAgentTools(room_id=sample_message.room_id)
        with caplog.at_level(logging.WARNING, logger=pydantic_adapter.__name__):
            await adapter.on_message(
                msg=sample_message,
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=tools.room_id,
            )
        if expected_metadata is None:
            assert "native run ended without a turn outcome" not in caplog.text
            assert tools.turn.replied
        else:
            assert expected_metadata in caplog.text
            assert not tools.turn.complete
        assert "private native reply" not in caplog.text
