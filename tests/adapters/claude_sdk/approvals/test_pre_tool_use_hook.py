from __future__ import annotations

import pytest

from band.adapters.claude_sdk import _pre_tool_use_continue_hook


class TestPreToolUseHook:
    """Tests for the PreToolUse hook that enables can_use_tool delegation."""

    @pytest.mark.asyncio
    async def test_hook_forces_permission_decision_ask(self):
        result = await _pre_tool_use_continue_hook(None, None, None)
        assert result == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
            }
        }
