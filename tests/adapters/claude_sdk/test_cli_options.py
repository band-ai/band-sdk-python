"""CLI launch options a host sets on the adapter reach the Claude CLI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import _configure_can_use_tool

from band.adapters.claude_sdk import (
    SDK_OWNED_CLI_FLAGS,
    ClaudeApprovalOptions,
    ClaudeCLIOptions,
    ClaudeSDKAdapterConfig,
)
from band.core.types import Emit
from tests.adapters.claude_sdk.helpers import SEND_MESSAGE_MCP_NAME, ClaudeRoom
from tests.paths import host_absolute_path

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def _cli_options(
    room: ClaudeRoom, session_id: str | None = None
) -> ClaudeAgentOptions:
    """The options the room's CLI process was started with."""
    room.claude.script([room.model_reply("Hello.")])
    await room.send("hi", session_id=session_id)
    [session] = room.claude.sessions
    return session.options


def _emitted_flags(options: ClaudeAgentOptions) -> set[str]:
    """The ``--flags`` the SDK's own connect path would launch the CLI with."""
    transport = SubprocessCLITransport(
        prompt="", options=_configure_can_use_tool(options)
    )
    # _build_command refuses to run until connect() has located the binary.
    transport._cli_path = "claude"
    return {
        token.removeprefix("--").partition("=")[0]
        for token in transport._build_command()
        if token.startswith("--")
    }


async def test_cli_options_reach_the_cli(claude_room: OpenRoom, tmp_path: Path) -> None:
    cli = ClaudeCLIOptions(
        cli_path=host_absolute_path("opt", "claude", "bin", "claude"),
        plugin_dirs=(str(tmp_path / "plugin"),),
        add_dirs=(str(tmp_path / "shared"),),
        env={"ANTHROPIC_API_KEY": "per-agent-key"},
        extra_args={"debug-to-stderr": None},
    )
    room = await claude_room(ClaudeSDKAdapterConfig(cli=cli))

    options = await _cli_options(room)

    assert options.plugins == [{"type": "local", "path": str(tmp_path / "plugin")}]
    assert options.cli_path == cli.cli_path
    assert options.env == {"ANTHROPIC_API_KEY": "per-agent-key"}
    assert options.add_dirs == [str(tmp_path / "shared")]
    assert options.extra_args == {"debug-to-stderr": None}


async def test_plugins_leave_band_wiring_in_place(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await claude_room(
        ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(plugin_dirs=(str(tmp_path),)))
    )

    options = await _cli_options(room)

    assert list(options.mcp_servers) == ["band"]
    assert SEND_MESSAGE_MCP_NAME in options.allowed_tools
    assert options.setting_sources == []


async def test_sdk_owned_flags_match_what_the_adapter_makes_the_sdk_emit(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await claude_room(
        ClaudeSDKAdapterConfig(
            model="sonnet",
            fallback_model="haiku",
            effort="high",
            max_thinking_tokens=1024,
            cwd=tmp_path,
            approvals=ClaudeApprovalOptions(mode="manual"),
            cli=ClaudeCLIOptions(
                plugin_dirs=(str(tmp_path),), add_dirs=(str(tmp_path),)
            ),
        )
    )

    options = await _cli_options(room, session_id="sess-1")

    assert _emitted_flags(options) == SDK_OWNED_CLI_FLAGS


@pytest.mark.parametrize(
    ("emit", "display"),
    [(Emit.THOUGHTS, "summarized"), (Emit.TOOL_CALLS, None)],
    ids=["thoughts-posted", "thoughts-not-posted"],
)
async def test_thinking_text_is_requested_only_when_thoughts_are_posted(
    claude_room: OpenRoom, emit: Emit, display: str | None
) -> None:
    room = await claude_room(
        ClaudeSDKAdapterConfig(max_thinking_tokens=1024), emit=emit
    )

    options = await _cli_options(room)

    assert options.thinking is not None
    assert options.thinking.get("display") == display


@pytest.mark.parametrize(
    "flag", ["mcp-config", "effort", "settings", "dangerously-skip-permissions"]
)
def test_extra_args_cannot_replace_adapter_owned_flags(flag: str) -> None:
    with pytest.raises(ValueError, match="adapter-owned CLI flags"):
        ClaudeCLIOptions(extra_args={flag: "x"})


def test_extra_args_keys_are_bare_flag_names() -> None:
    with pytest.raises(ValueError, match="bare flag names"):
        ClaudeCLIOptions(extra_args={"--debug-to-stderr": None})
