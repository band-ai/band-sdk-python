"""Thinking-block MESSAGE_END content is posted as thought telemetry."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit, MessageType
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.adapters.omp.kit import ThinkingBlockRuntime, run_turn


class TestThinkingBlock:
    @pytest.mark.asyncio
    async def test_thinking_block_is_posted_and_text_closes_as_thought(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = ThinkingBlockRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.THOUGHTS})
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            thoughts = [
                event["content"] for event in events_of_type(tools, MessageType.THOUGHT)
            ]
            assert thoughts == ["Internal reasoning", "Visible answer"]
        finally:
            await adapter.cleanup_all()
