"""Codex adapter: ``turn/diff/updated`` metadata is bounded in UTF-8 bytes, not chars."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    _MAX_DIFF_METADATA_BYTES,
    CodexAdapterConfig,
)
from band.integrations.codex.types import CodexSessionState
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestDiffByteCap:
    """``turn/diff/updated`` metadata is bounded in UTF-8 bytes, not chars."""

    @pytest.mark.asyncio
    async def test_multibyte_diff_respects_byte_budget(self) -> None:
        """A diff built from 4-byte codepoints is capped to the byte budget,
        not the character budget (which would be ~4× larger on the wire)."""

        # Each emoji is 4 UTF-8 bytes; use ~1.5× the byte budget worth.
        emoji = "\U0001f600"
        diff_chars = (_MAX_DIFF_METADATA_BYTES // 4) + 5000
        big_diff = emoji * diff_chars
        assert len(big_diff.encode("utf-8")) > _MAX_DIFF_METADATA_BYTES

        events = [
            event_notification(
                "turn/diff/updated",
                {"diff": big_diff, "files": ["src/app.py"]},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_diff_events=True)
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        diff_events = [
            e
            for e in tools.events_sent
            if e["metadata"].get("codex_event_type") == "turn_diff"
        ]
        assert len(diff_events) == 1
        meta = diff_events[0]["metadata"]
        emitted = meta["codex_diff"]
        # The emitted diff (including the truncation marker) stays within a
        # small overhead of the byte budget — nowhere near 4× it.
        assert len(emitted.encode("utf-8")) <= _MAX_DIFF_METADATA_BYTES + 256
        assert meta["codex_diff_truncated"] is True
        assert meta["codex_diff_original_bytes"] > _MAX_DIFF_METADATA_BYTES
