"""Options a host embedding the Claude CLI sets on the adapter reach the CLI."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from band.adapters.claude_sdk import ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import SEND_MESSAGE_MCP_NAME, ClaudeRoom

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


async def _cli_options(room: ClaudeRoom):
    """The options the room's CLI process was started with."""
    room.claude.script([room.model_reply("Hello.")])
    await room.send("hi")
    [session] = room.claude.sessions
    return session.options


@pytest.mark.parametrize("mode", ["auto", "dontAsk"])
async def test_headless_permission_modes_reach_the_cli(
    claude_room: OpenRoom, mode: str
) -> None:
    options = await _cli_options(await claude_room(permission_mode=mode))
    assert options.permission_mode == mode


async def test_passthrough_options_reach_the_cli(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await claude_room(
        plugin_dirs=[str(tmp_path / "plugin")],
        cli_path="/opt/claude/bin/claude",
        env={"ANTHROPIC_API_KEY": "per-agent-key"},
        add_dirs=[str(tmp_path / "shared")],
        extra_args={"debug-to-stderr": None},
    )

    options = await _cli_options(room)

    assert options.plugins == [{"type": "local", "path": str(tmp_path / "plugin")}]
    assert options.cli_path == "/opt/claude/bin/claude"
    assert options.env == {"ANTHROPIC_API_KEY": "per-agent-key"}
    assert options.add_dirs == [str(tmp_path / "shared")]
    assert options.extra_args == {"debug-to-stderr": None}


async def test_plugins_leave_band_wiring_in_place(
    claude_room: OpenRoom, tmp_path: Path
) -> None:
    room = await claude_room(plugin_dirs=[str(tmp_path)])

    options = await _cli_options(room)

    assert list(options.mcp_servers) == ["band"]
    assert SEND_MESSAGE_MCP_NAME in options.allowed_tools
    assert options.setting_sources == []
    assert room.chat == ["Hello."]


async def test_omitted_passthrough_keeps_sdk_defaults(claude_room: OpenRoom) -> None:
    options = await _cli_options(await claude_room())
    assert (
        options.plugins,
        options.cli_path,
        options.env,
        options.add_dirs,
        options.extra_args,
    ) == ([], None, {}, [], {})


@pytest.mark.parametrize("flag", ["mcp-config", "--permission-mode", "allowedTools"])
def test_extra_args_cannot_replace_adapter_owned_flags(flag: str) -> None:
    with pytest.raises(ValueError, match="adapter-owned CLI flags"):
        ClaudeSDKAdapter(extra_args={flag: "x"})
