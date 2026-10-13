"""Codex adapter: _session_approval_key behaviour across method/param shapes."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.integrations.codex.types import CodexSessionState
from tests.adapters.codex.kit import (
    ToolSchemaFakeTools,
    make_platform_message,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    await_released_turn,
    event_request,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestSessionApprovalKeying:
    """_session_approval_key behaviour across method/param shapes."""

    def test_file_change_session_key_requires_paths(self) -> None:
        """/approve-session must refuse fileChange requests with no paths."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"reason": "something vague"},
        )
        assert key == ""

    def test_file_change_session_key_uses_paths_when_present(self) -> None:
        """fileChange session key includes sorted path list for stable matching."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key1 = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"changes": [{"path": "b.py"}, {"path": "a.py"}]},
        )
        key2 = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"changes": [{"path": "a.py"}, {"path": "b.py"}]},
        )
        assert key1 == key2
        assert key1 == "fileChange:a.py|b.py"

    def test_file_change_session_key_handles_top_level_paths(self) -> None:
        """fileChange session key also picks up top-level path/paths fields."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        key = adapter._session_approval_key(
            "item/fileChange/requestApproval",
            {"paths": ["src/foo.py", "src/bar.py"]},
        )
        assert "src/foo.py" in key
        assert "src/bar.py" in key

    def test_unknown_approval_method_returns_empty_key(self) -> None:
        """Session-level approval refuses unknown methods rather than bucketing them."""
        adapter = CodexAdapter(config=CodexAdapterConfig())
        assert adapter._session_approval_key("item/unknown/requestApproval", {}) == ""

    @pytest.mark.asyncio
    async def test_approve_session_refused_for_fileChange_without_paths(self) -> None:
        """/approve-session for a fileChange request with no paths is rejected."""
        events = [
            event_request(
                88,
                "item/fileChange/requestApproval",
                {"reason": "write something"},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                approval_mode="manual",
                approval_wait_timeout_s=0.05,
                approval_timeout_decision="decline",
            ),
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
        await await_released_turn(adapter, "room-1")

        # Manual approval times out and the pending record is cleared,
        # so /approve-session reports no pending approvals rather than
        # storing an empty session key.
        tools.messages_sent.clear()
        await deliver_message(
            adapter,
            make_platform_message(content="/approve-session 88"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        assert any("No pending approvals" in m["content"] for m in tools.messages_sent)
        assert "room-1" not in adapter._session_approved
