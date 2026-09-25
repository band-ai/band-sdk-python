from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter


class TestApprovalOnStarted:
    """Tests that on_started passes can_use_tool_factory to session manager."""

    @pytest.mark.asyncio
    async def test_passes_factory_when_approval_enabled(self):
        adapter = ClaudeSDKAdapter(approval_mode="manual")

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("can_use_tool_factory") is not None

    @pytest.mark.asyncio
    async def test_no_factory_when_approval_disabled(self):
        adapter = ClaudeSDKAdapter()  # approval_mode=None

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            call_kwargs = mock_cls.call_args.kwargs
            assert call_kwargs.get("can_use_tool_factory") is None

    @pytest.mark.asyncio
    async def test_sets_pre_tool_use_hook_when_approval_enabled(self):
        """PreToolUse hook must be set so the SDK delegates to can_use_tool."""
        adapter = ClaudeSDKAdapter(approval_mode="auto_accept")

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            # The first positional arg is the ClaudeAgentOptions
            sdk_options = mock_cls.call_args.args[0]
            assert sdk_options.hooks is not None
            assert "PreToolUse" in sdk_options.hooks
            assert len(sdk_options.hooks["PreToolUse"]) == 1

    @pytest.mark.asyncio
    async def test_no_hooks_when_approval_disabled(self):
        adapter = ClaudeSDKAdapter()  # approval_mode=None

        with patch("band.adapters.claude_sdk.ClaudeSessionManager") as mock_cls:
            mock_cls.return_value = MagicMock()
            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_cls.call_args.args[0]
            assert sdk_options.hooks is None
