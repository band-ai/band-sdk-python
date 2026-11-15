"""TransparentPassthrough behavior."""

from __future__ import annotations

import pytest
from band_sdk_core import AgentFailure

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


class TestTransparentPassthrough:
    @pytest.mark.asyncio
    async def test_other_methods_forward_unchanged(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_event(content="ping", message_type="thought")
        await wrapper.add_participant("@svc/bot")

        inner.send_event.assert_awaited_once_with(
            content="ping", message_type="thought"
        )
        inner.add_participant.assert_awaited_once_with("@svc/bot")

    @pytest.mark.asyncio
    async def test_send_failure_forwards_unchanged(self):
        """No dedup special-casing for send_failure -- __getattr__ forwards
        it straight through, same as every other AgentToolsProtocol method."""
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)
        failure = AgentFailure("codex", "boom")

        await wrapper.send_failure(failure)

        inner.send_failure.assert_awaited_once_with(failure)

    def test_attributes_forward_unchanged(self):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)
        assert wrapper.participants == ["p1", "p2"]
