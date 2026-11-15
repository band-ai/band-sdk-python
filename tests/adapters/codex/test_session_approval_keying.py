"""Codex adapter: session approval keying."""

from __future__ import annotations

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.adapters.codex.approvals import session_approval_key
from band.integrations.codex.types import CodexApprovalMethod, CodexSessionState
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    make_platform_message,
    send_bootstrap,
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
    """Which requests ``/approve-session`` can match later ones by."""

    def test_file_change_session_key_requires_paths(self) -> None:
        """/approve-session must refuse fileChange requests with no paths."""
        key = session_approval_key(
            "item/fileChange/requestApproval",
            {"reason": "something vague"},
            "full_command",
        )
        assert key == ""

    def test_file_change_session_key_uses_paths_when_present(self) -> None:
        """fileChange session key includes sorted path list for stable matching."""
        key1 = session_approval_key(
            "item/fileChange/requestApproval",
            {"changes": [{"path": "b.py"}, {"path": "a.py"}]},
            "full_command",
        )
        key2 = session_approval_key(
            "item/fileChange/requestApproval",
            {"changes": [{"path": "a.py"}, {"path": "b.py"}]},
            "full_command",
        )
        assert key1 == key2
        assert key1 == "fileChange:a.py|b.py"

    def test_file_change_session_key_handles_top_level_paths(self) -> None:
        """fileChange session key also picks up top-level path/paths fields."""
        key = session_approval_key(
            "item/fileChange/requestApproval",
            {"paths": ["src/foo.py", "src/bar.py"]},
            "full_command",
        )
        assert "src/foo.py" in key
        assert "src/bar.py" in key

    def test_unknown_approval_method_returns_empty_key(self) -> None:
        """Session-level approval refuses unknown methods rather than bucketing them."""
        assert (
            session_approval_key("item/unknown/requestApproval", {}, "full_command")
            == ""
        )

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

        await send_bootstrap(adapter, tools=tools)
        await await_released_turn(adapter, ROOM_ID)

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
            room_id=ROOM_ID,
        )
        assert any("No pending approvals" in m["content"] for m in tools.messages_sent)
        assert adapter._approvals.session_patterns(ROOM_ID) == []

    def test_session_approval_key_empty_prevents_wildcard_match(self) -> None:
        """Empty session key from missing command cannot match any session set."""
        key = session_approval_key(
            CodexApprovalMethod.COMMAND_EXECUTION, {}, "full_command"
        )
        # Empty key is falsy, so `key and key in session_set` is always False
        assert not key
        assert not (key and key in {"commandExecution:npm"})

    def test_session_approval_key_full_command_by_default(self) -> None:
        """Session approval key includes full command string by default."""
        key = session_approval_key(
            CodexApprovalMethod.COMMAND_EXECUTION,
            {"command": "npm test"},
            "full_command",
        )
        assert key == "commandExecution:npm test"

    def test_session_approval_key_binary_granularity(self) -> None:
        """Session approval key includes only binary when granularity is 'binary'."""
        key = session_approval_key(
            CodexApprovalMethod.COMMAND_EXECUTION, {"command": "npm test"}, "binary"
        )
        assert key == "commandExecution:npm"

    def test_session_approval_key_empty_for_missing_command(self) -> None:
        """Session approval key returns empty when command is missing (no wildcard)."""
        key = session_approval_key(
            CodexApprovalMethod.COMMAND_EXECUTION, {}, "full_command"
        )
        assert key == ""

    def test_session_approval_key_empty_for_file_changes_without_paths(self) -> None:
        """A fileChange request without paths has no session key, so
        /approve-session can't approve every future change."""
        key = session_approval_key(
            "item/fileChange/requestApproval", {"reason": "update"}, "full_command"
        )
        assert key == ""
