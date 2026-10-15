"""An unsettled native run over a batch names every message it consumed."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.function import AgentInfo, FunctionModel

import band.adapters.pydantic_ai as pydantic_adapter
from band.adapters.pydantic_ai import PydanticAIAdapter
from band.core.protocols import TurnResultAlreadyReported
from band.testing import FakeAgentTools
from tests.framework_conformance.batchprobes import BATCH, delivery_input


class TestBatchDiagnostics:
    async def test_an_unsettled_batch_run_names_every_original(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str]:
            yield "private native reply"

        adapter = PydanticAIAdapter(llm=FunctionModel(stream_function=stream))
        await adapter.on_started("Probe", "probe")
        inp = replace(await delivery_input(BATCH), tools=FakeAgentTools())

        with (
            caplog.at_level(logging.WARNING, logger=pydantic_adapter.__name__),
            pytest.raises(TurnResultAlreadyReported),
        ):
            await adapter.on_messages(inp)

        assert f"messages {','.join(o.id for o in BATCH)}:" in caplog.text
