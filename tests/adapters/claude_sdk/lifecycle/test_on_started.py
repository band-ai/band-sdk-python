from __future__ import annotations

import re
from unittest.mock import MagicMock, patch

import pytest

from band.adapters.claude_sdk import (
    CLAUDE_SDK_MAX_BUFFER_BYTES,
    DEFAULT_MODEL,
    NATIVE_TOOL_MATCHER,
    TOOL_SEARCH,
    ClaudeSDKAdapter,
)
from band.runtime.tools import MAX_INLINE_IMAGE_BYTES, MCP_TOOL_PREFIX


class TestOnStarted:
    """Tests for on_started() method."""

    @pytest.mark.asyncio
    async def test_creates_mcp_server_and_session_manager(self):
        """Should create MCP server and session manager on start."""
        adapter = ClaudeSDKAdapter()

        # Mock the session manager
        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager = MagicMock()
            mock_manager_class.return_value = mock_manager

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            assert adapter.agent_name == "TestBot"
            assert adapter.agent_description == "A test bot"
            assert adapter._session_manager is not None
            assert adapter._mcp_server is not None

    @pytest.mark.asyncio
    async def test_default_options_pin_default_model(self):
        """Default ClaudeSDKAdapter() pins DEFAULT_MODEL (the npm `claude`
        binary's auto-selection fails under API-key auth), fallback_model=None."""
        adapter = ClaudeSDKAdapter()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model == DEFAULT_MODEL
            assert sdk_options.fallback_model is None

    @pytest.mark.asyncio
    async def test_max_buffer_size_exceeds_claude_agent_sdks_default(self):
        """Reproduced live: band_read_room_file inlined a 737.8 KB JPEG as
        base64 (~4/3 size increase) inside one JSON-per-line message from the
        Claude CLI subprocess -- comfortably clearing claude_agent_sdk's
        stdio transport's default max_buffer_size of 1 MiB
        (claude_agent_sdk._internal.transport.subprocess_cli.
        _DEFAULT_MAX_BUFFER_SIZE) -- and that fatally dropped the whole CLI
        connection, not just the one tool call. The configured buffer must
        clear both the library's real default and the base64-inflated size
        of the largest image band_read_room_file advertises inlining
        (MAX_INLINE_IMAGE_BYTES); anything less reopens the same crash.
        """
        adapter = ClaudeSDKAdapter()

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            claude_agent_sdk_default_buffer_bytes = 1024 * 1024
            largest_inline_image_base64_bytes = MAX_INLINE_IMAGE_BYTES * 4 // 3

            assert sdk_options.max_buffer_size == CLAUDE_SDK_MAX_BUFFER_BYTES
            assert sdk_options.max_buffer_size > claude_agent_sdk_default_buffer_bytes
            assert sdk_options.max_buffer_size > largest_inline_image_base64_bytes

    @pytest.mark.asyncio
    async def test_explicit_model_is_forwarded(self):
        """Explicit model= should land in ClaudeAgentOptions.model."""
        adapter = ClaudeSDKAdapter(model="opus")

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model == "opus"
            assert sdk_options.fallback_model is None

    @pytest.mark.asyncio
    async def test_fallback_model_is_forwarded(self):
        """Both model and fallback_model must reach ClaudeAgentOptions.

        Regression guard: catches a missed `fallback_model=self.fallback_model`
        in the ClaudeAgentOptions construction in on_started().
        """
        adapter = ClaudeSDKAdapter(model="opus", fallback_model="sonnet")

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.model == "opus"
            assert sdk_options.fallback_model == "sonnet"

    @pytest.mark.asyncio
    async def test_effort_is_forwarded(self):
        """effort= should land in ClaudeAgentOptions.effort."""
        adapter = ClaudeSDKAdapter(effort="xhigh")

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            assert sdk_options.effort == "xhigh"

    @pytest.mark.asyncio
    async def test_approval_hook_matches_native_tools_only(self):
        """Manual approval must not intercept the adapter's own MCP tools."""
        adapter = ClaudeSDKAdapter(approval_mode="manual")

        with patch(
            "band.adapters.claude_sdk.ClaudeSessionManager"
        ) as mock_manager_class:
            mock_manager_class.return_value = MagicMock()

            await adapter.on_started(
                agent_name="TestBot", agent_description="A test bot"
            )

            sdk_options = mock_manager_class.call_args[0][0]
            [matcher] = sdk_options.hooks["PreToolUse"]

            assert matcher.matcher == NATIVE_TOOL_MATCHER
            assert re.fullmatch(matcher.matcher, "Bash")
            assert not re.fullmatch(
                matcher.matcher, f"{MCP_TOOL_PREFIX}band_send_message"
            )
            # Claude Code loads deferred tool definitions (Band's included)
            # through ToolSearch; a room must not approve every lookup.
            assert not re.fullmatch(matcher.matcher, TOOL_SEARCH)
            assert TOOL_SEARCH in sdk_options.allowed_tools
            assert re.fullmatch(matcher.matcher, "ToolSearchX")
