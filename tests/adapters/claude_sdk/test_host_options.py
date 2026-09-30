"""Options a host embedding the Claude CLI sets on the adapter reach the CLI."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import _configure_can_use_tool

from band.adapters.claude_sdk import SDK_OWNED_CLI_FLAGS, ClaudeCLIOptions
from tests.adapters.claude_sdk.helpers import SEND_MESSAGE_MCP_NAME, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def _cli_options(room: ClaudeRoom, session_id: str | None = None):
    """The options the room's CLI process was started with."""
    room.claude.script([room.model_reply("Hello.")])
    await room.send("hi", session_id=session_id)
    [session] = room.claude.sessions
    return session.options


@pytest.mark.parametrize("mode", ["auto", "dontAsk"])
async def test_headless_permission_modes_reach_the_cli(
    claude_room: OpenRoom, mode: str
) -> None:
    options = await _cli_options(await claude_room(permission_mode=mode))
    assert options.permission_mode == mode


@pytest.mark.parametrize(
    "cli_path",
    [
        pytest.param(
            "/opt/claude/bin/claude",
            id="posix",
            marks=pytest.mark.skipif(os.name == "nt", reason="POSIX path semantics"),
        ),
        pytest.param(
            r"C:\Program Files\Claude\claude.exe",
            id="windows",
            marks=pytest.mark.skipif(os.name != "nt", reason="Windows path semantics"),
        ),
    ],
)
async def test_passthrough_options_reach_the_cli(
    claude_room: OpenRoom, tmp_path: Path, cli_path: str
) -> None:
    cli = ClaudeCLIOptions(
        cli_path=cli_path,
        plugin_dirs=(str(tmp_path / "plugin"),),
        add_dirs=(str(tmp_path / "shared"),),
        env={"ANTHROPIC_API_KEY": "per-agent-key"},
        extra_args={"debug-to-stderr": None},
    )
    room = await claude_room(cli=cli)

    options = await _cli_options(room)

    assert options.plugins == [{"type": "local", "path": str(tmp_path / "plugin")}]
    assert options.cli_path == cli_path
    assert options.env == {"ANTHROPIC_API_KEY": "per-agent-key"}
    assert options.add_dirs == [str(tmp_path / "shared")]
    assert options.extra_args == {"debug-to-stderr": None}


async def test_plugins_leave_band_wiring_in_place(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await claude_room(cli=ClaudeCLIOptions(plugin_dirs=(str(tmp_path),)))

    options = await _cli_options(room)

    assert list(options.mcp_servers) == ["band"]
    assert SEND_MESSAGE_MCP_NAME in options.allowed_tools
    assert options.setting_sources == []


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


async def test_sdk_owned_flags_match_what_the_adapter_makes_the_sdk_emit(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await claude_room(
        model="sonnet",
        fallback_model="haiku",
        effort="high",
        max_thinking_tokens=1024,
        cwd=str(tmp_path),
        approval_mode="manual",
        cli=ClaudeCLIOptions(plugin_dirs=(str(tmp_path),), add_dirs=(str(tmp_path),)),
    )

    options = await _cli_options(room, session_id="sess-1")

    assert _emitted_flags(options) == SDK_OWNED_CLI_FLAGS


@pytest.mark.parametrize("flag", ["mcp-config", "permission-mode", "allowedTools"])
def test_extra_args_cannot_replace_adapter_owned_flags(flag: str) -> None:
    with pytest.raises(ValueError, match="adapter-owned CLI flags"):
        ClaudeCLIOptions(extra_args={flag: "x"})


def test_extra_args_keys_are_bare_flag_names() -> None:
    with pytest.raises(ValueError, match="bare flag names"):
        ClaudeCLIOptions(extra_args={"--debug-to-stderr": None})
