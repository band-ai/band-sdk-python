"""DedupHitLogging behavior."""

from __future__ import annotations

import pytest

from band.integrations.claude_sdk.dedup_tools import (
    DedupingAgentTools,
)
from tests.integrations.claude_sdk.dedup.kit import _make_inner


class TestDedupHitLogging:
    @pytest.mark.asyncio
    async def test_duplicate_emits_warning(self, caplog):
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hello", ["alice"])
        with caplog.at_level(
            "WARNING", logger="band.integrations.claude_sdk.dedup_tools"
        ):
            await wrapper.send_message("hello", ["alice"])

        assert any(
            "dedup" in rec.message.lower() and rec.levelname == "WARNING"
            for rec in caplog.records
        ), (
            f"expected WARNING log on dedup hit, got: {[r.message for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_warning_includes_label_when_provided(self, caplog):
        """The adapter passes ``label=room_id`` so an operator triaging a
        dedup storm in production can map a warning back to one room.
        Without it the log is ``suppressing duplicate send`` with no
        identifier and is useless across a multi-room tenant.
        """
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner, label="room-abc")

        await wrapper.send_message("hello", ["alice"])
        with caplog.at_level(
            "WARNING", logger="band.integrations.claude_sdk.dedup_tools"
        ):
            await wrapper.send_message("hello", ["alice"])

        assert any("room-abc" in rec.getMessage() for rec in caplog.records), (
            f"expected room-abc label in warning, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    @pytest.mark.asyncio
    async def test_warning_omits_label_brackets_when_unset(self, caplog):
        """No label → no empty ``[]`` in the log line."""
        inner = _make_inner()
        wrapper = DedupingAgentTools(inner)

        await wrapper.send_message("hello", ["alice"])
        with caplog.at_level(
            "WARNING", logger="band.integrations.claude_sdk.dedup_tools"
        ):
            await wrapper.send_message("hello", ["alice"])

        for rec in caplog.records:
            if "dedup" in rec.getMessage().lower():
                assert "[]" not in rec.getMessage()
