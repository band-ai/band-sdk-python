"""Claude subprocess construction, including SDK permission normalization."""

from __future__ import annotations

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport import Transport
from claude_agent_sdk.types import _configure_can_use_tool

from band.integrations.claude_sdk.retained import RetainedCLITransport


def create_transport(options: ClaudeAgentOptions) -> Transport:
    """Keep permission routing identical to the SDK's default transport."""
    return RetainedCLITransport(prompt="", options=_configure_can_use_tool(options))
