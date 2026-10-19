"""Claude subprocess construction, including SDK permission normalization."""

from __future__ import annotations

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport import Transport
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import _configure_can_use_tool


def create_transport(options: ClaudeAgentOptions) -> Transport:
    """Keep permission routing identical to the SDK's default transport."""
    return SubprocessCLITransport(prompt="", options=_configure_can_use_tool(options))
