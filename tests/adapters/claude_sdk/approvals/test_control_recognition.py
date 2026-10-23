"""Claude room commands become controls only when approvals are enabled."""

from __future__ import annotations

import pytest

from band.adapters.claude_sdk import (
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
    ClaudeSDKCommand,
)
from tests.adapters.claude_sdk.helpers import with_approvals
from tests.adapters.codex.kit import make_platform_message


class TestClaudeControlRecognition:
    @pytest.mark.parametrize(
        ("config", "content", "recognized"),
        [
            (ClaudeSDKAdapterConfig(), f"/{ClaudeSDKCommand.APPROVE} a-1", False),
            (ClaudeSDKAdapterConfig(), f"/{ClaudeSDKCommand.STATUS}", False),
            (with_approvals(), f"/{ClaudeSDKCommand.APPROVE} a-1", True),
            (
                with_approvals(),
                f"@owner/claude /{ClaudeSDKCommand.DECLINE.capitalize()} a-1",
                True,
            ),
            (with_approvals(), f"/{ClaudeSDKCommand.APPROVALS}", True),
            (with_approvals(), f"/{ClaudeSDKCommand.STATUS}", True),
            (with_approvals(), f"do not /{ClaudeSDKCommand.APPROVE} a-1", False),
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
