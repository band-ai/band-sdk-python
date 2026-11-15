"""Declining a batch turn ends the native run like declining a single message."""

from __future__ import annotations

from dataclasses import replace

from band.adapters.pydantic_ai import PydanticAIAdapter
from band.testing import FakeAgentTools, reported_failures
from tests.adapters.pydanticai.kit import DecliningModel
from tests.framework_conformance.batchprobes import BATCH, delivery_input


class TestBatchDecline:
    async def test_declining_a_batch_ends_the_run_after_one_model_request(
        self,
    ) -> None:
        model = DecliningModel()
        adapter = PydanticAIAdapter(llm=model.model)
        await adapter.on_started("Probe", "probe")
        tools = FakeAgentTools()
        inp = replace(await delivery_input(BATCH), tools=tools)

        await adapter.on_messages(inp)

        assert len(inp.messages) == len(BATCH)
        assert model.requests == 1
        assert tools.turn.replied
        assert reported_failures(tools) == []
