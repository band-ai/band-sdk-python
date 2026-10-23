"""Claude room commands become controls only when approvals are enabled."""

from __future__ import annotations

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from tests.adapters.claude_sdk.helpers import with_approvals
from tests.adapters.codex.kit import make_platform_message


class TestClaudeControlRecognition:
    @pytest.mark.parametrize(
        ("config", "content", "recognized"),
        [
            (ClaudeSDKAdapterConfig(), "/approve a-1", False),
            (ClaudeSDKAdapterConfig(), "/status", False),
            (with_approvals(), "/approve a-1", True),
            (with_approvals(), "@owner/claude /Decline a-1", True),
            (with_approvals(), "/approvals", True),
            (with_approvals(), "/status", True),
            (with_approvals(), "do not /approve a-1", False),
            (with_approvals(), "/unknown", False),
            (with_approvals(), "", False),
        ],
    )
    def test_recognizes_enabled_room_commands(
        self, config: ClaudeSDKAdapterConfig, content: str, recognized: bool
    ) -> None:
        adapter = ClaudeSDKAdapter(config)

        assert (
            adapter.is_control_message(make_platform_message(content=content))
            is recognized
        )
