"""String assistant MESSAGE_END content is posted as thought telemetry."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit, MessageType
from band.testing import FakeAgentTools
from band.testing.fake_tools import events_of_type
from tests.adapters.omp.kit import (
    ClosingThoughtAfterReplyRuntime,
    NonAssistantMessageRuntime,
    StringMessageRuntime,
    WhitespaceMessageRuntime,
    run_turn,
)


class TestStringThought:
    @pytest.mark.asyncio
    async def test_string_assistant_content_is_posted_as_thought(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = StringMessageRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.THOUGHTS})
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            thoughts = [
                event["content"] for event in events_of_type(tools, MessageType.THOUGHT)
            ]
            assert "Visible assistant text" in thoughts
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_whitespace_assistant_content_is_not_posted(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = WhitespaceMessageRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.THOUGHTS})
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert events_of_type(tools, MessageType.THOUGHT) == []
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_non_assistant_message_is_not_posted_as_thought(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = NonAssistantMessageRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.THOUGHTS})
        tools = FakeAgentTools()
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
            assert events_of_type(tools, MessageType.THOUGHT) == []
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_closing_thought_after_reply_is_not_posted(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = ClosingThoughtAfterReplyRuntime()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.THOUGHTS})
        tools = FakeAgentTools()
        try:
            await run_turn(adapter, tools)
            thoughts = [
                event["content"] for event in events_of_type(tools, MessageType.THOUGHT)
            ]
            assert "Closing thought after reply" not in thoughts
        finally:
            await adapter.cleanup_all()
