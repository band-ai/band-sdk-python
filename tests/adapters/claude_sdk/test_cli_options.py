"""CLI launch options a host sets on the adapter reach the Claude CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from claude_agent_sdk.types import (
    PermissionResultAllow,
    ToolPermissionContext,
)

from band.adapters.claude_sdk import (
    BAND_MCP_TOKEN_ENV,
    RESERVED_CLI_FLAGS,
    SDK_OWNED_CLI_FLAGS,
    ClaudeApprovalOptions,
    ClaudeCLIOptions,
    ClaudeSDKAdapterConfig,
)
from band.core.types import Emit
from band.integrations.claude_sdk.transport import create_transport
from tests.adapters.claude_sdk.helpers import (
    SEND_MESSAGE_MCP_NAME,
    ClaudeRoom,
    OpenRoom,
    sdk_cli_argv,
)
from tests.paths import host_absolute_path


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
    return {
        token.removeprefix("--").partition("=")[0]
        for token in sdk_cli_argv(options)
        if token.startswith("--")
    }


class TestCliOptions:
    def test_owned_transport_preserves_sdk_permission_routing(self) -> None:
        async def allow(
            tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
        ) -> PermissionResultAllow:
            return PermissionResultAllow()

        options = ClaudeAgentOptions(cli_path=sys.executable, can_use_tool=allow)
        transport = create_transport(options)
        assert isinstance(transport, SubprocessCLITransport)
        command = transport._build_command()
        assert command[command.index("--permission-prompt-tool") + 1] == "stdio"
        assert options.permission_prompt_tool_name is None
        assert options.can_use_tool is allow

    async def test_cli_options_reach_the_cli(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
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
        assert options.env == {
            "ANTHROPIC_API_KEY": "per-agent-key",
            BAND_MCP_TOKEN_ENV: room.mcp_backend.auth_token,
        }
        assert options.add_dirs == [str(tmp_path / "shared")]
        assert options.extra_args == {"debug-to-stderr": None}

    async def test_plugins_leave_band_wiring_in_place(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        room = await claude_room(
            ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(plugin_dirs=(str(tmp_path),)))
        )

        options = await _cli_options(room)

        assert list(options.mcp_servers) == ["band"]
        assert SEND_MESSAGE_MCP_NAME in options.allowed_tools
        assert options.setting_sources == []

    async def test_every_canonically_named_flag_the_cli_gets_is_adapter_owned(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        # What the SDK may put on the child's argv is the adapter's own vocabulary plus
        # the host's explicitly raw flags. The reserved check rejects a raw *exact-name*
        # duplicate of an adapter-owned flag; a malformed spelling (e.g. ``settings=x``,
        # with an embedded ``=``) is not the canonical name and passes the exact-match
        # check -- a pre-existing validator gap, not introduced here (tracked separately).
        # This test pins the canonical-name guarantee only.
        settings = tmp_path / "team-settings.json"
        settings.write_text('{"model": "sonnet"}')
        cli = ClaudeCLIOptions(
            plugin_dirs=(str(tmp_path),),
            add_dirs=(str(tmp_path),),
            extra_args={"debug-to-stderr": None},
            settings=str(settings),
        )
        room = await claude_room(
            ClaudeSDKAdapterConfig(
                model="sonnet",
                fallback_model="haiku",
                effort="high",
                max_thinking_tokens=1024,
                cwd=tmp_path,
                approvals=ClaudeApprovalOptions(mode="manual"),
                cli=cli,
            )
        )

        options = await _cli_options(room, session_id="sess-1")

        emitted = _emitted_flags(options)
        # Every owned flag arrives through the adapter's typed configuration...
        assert emitted >= SDK_OWNED_CLI_FLAGS
        # ...and no canonical name outside that set appears except the host's raw
        # additions, so the adapter's typed flags cannot be duplicated by a raw
        # exact-name spelling.
        assert emitted <= SDK_OWNED_CLI_FLAGS | set(cli.extra_args)
        assert options.extra_args == {"debug-to-stderr": None}

    async def test_typed_settings_reaches_the_cli_exactly_once(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        settings = tmp_path / "team-settings.json"
        settings.write_text('{"model": "sonnet"}')
        room = await claude_room(
            ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(settings=str(settings)))
        )

        options = await _cli_options(room)

        assert options.settings == str(settings)
        argv = sdk_cli_argv(options)
        assert argv.count("--settings") == 1
        assert argv[argv.index("--settings") + 1] == str(settings)

    async def test_no_settings_flag_for_an_agent_that_selected_none(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(ClaudeSDKAdapterConfig())

        options = await _cli_options(room)

        assert options.settings is None
        assert "--settings" not in sdk_cli_argv(options)

    async def test_inline_json_settings_pass_through_untouched(
        self, claude_room: OpenRoom
    ) -> None:
        # The SDK accepts a path or inline JSON; the adapter hands the exact text to
        # the CLI and interprets nothing — no rewrite, no file access here.
        inline = '{"permissions": {"defaultMode": "plan"}}'
        room = await claude_room(
            ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(settings=inline))
        )

        options = await _cli_options(room)

        assert options.settings == inline
        argv = sdk_cli_argv(options)
        assert argv[argv.index("--settings") + 1] == inline

    async def test_settings_survives_fresh_resume_and_the_fresh_replacement(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        # options_for() copies base_options per room session, so the selection must
        # ride every way a session begins: a fresh room, the bootstrap resume of a
        # recorded session, and the fresh session that heals an unresumable one.
        settings = tmp_path / "team-settings.json"
        settings.write_text('{"model": "sonnet"}')
        config = ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(settings=str(settings)))
        first = await claude_room(config)
        second = await claude_room(config)
        third = await claude_room(config)
        first.claude.unresumable.add("sess-broken")
        first.claude.script(
            [first.model_reply("first")],
            [first.model_reply("resumed")],
            [first.model_reply("replaced")],
        )

        await first.send("one")
        await second.send("two", session_id="sess-from-history")
        await third.send("three", session_id="sess-broken")

        launches = first.claude.sessions
        assert [session.options.resume for session in launches] == [
            None,
            "sess-from-history",
            "sess-broken",
            None,
        ]
        assert all(session.options.settings == str(settings) for session in launches)
        assert first.chat + second.chat + third.chat == ["first", "resumed", "replaced"]

    async def test_settings_leaves_band_wiring_and_permission_routing_in_place(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        # A selected settings file must not widen what Band exposes: the MCP backend
        # stays the single "band" server, the reply tools stay allowed, host setting
        # sources stay closed, and the approval callback keeps its routing.
        settings = tmp_path / "team-settings.json"
        settings.write_text('{"model": "sonnet"}')
        room = await claude_room(
            ClaudeSDKAdapterConfig(
                approvals=ClaudeApprovalOptions(mode="manual"),
                cli=ClaudeCLIOptions(settings=str(settings)),
            )
        )

        options = await _cli_options(room)

        assert list(options.mcp_servers) == ["band"]
        assert SEND_MESSAGE_MCP_NAME in options.allowed_tools
        assert options.setting_sources == []
        assert options.can_use_tool is not None
        argv = sdk_cli_argv(options)
        assert argv[argv.index("--permission-prompt-tool") + 1] == "stdio"
        assert argv.count("--settings") == 1

    @pytest.mark.parametrize(
        ("emit", "display"),
        [(Emit.THOUGHTS, "summarized"), (Emit.TOOL_CALLS, None)],
        ids=["thoughts-posted", "thoughts-not-posted"],
    )
    async def test_thinking_text_is_requested_only_when_thoughts_are_posted(
        self, claude_room: OpenRoom, emit: Emit, display: str | None
    ) -> None:
        room = await claude_room(
            ClaudeSDKAdapterConfig(max_thinking_tokens=1024), emit=emit
        )

        options = await _cli_options(room)

        assert options.thinking is not None
        assert options.thinking.get("display") == display

    @pytest.mark.parametrize("flag", sorted(RESERVED_CLI_FLAGS))
    def test_extra_args_cannot_replace_adapter_owned_flags(self, flag: str) -> None:
        with pytest.raises(ValueError, match="adapter-owned CLI flags"):
            ClaudeCLIOptions(extra_args={flag: "x"})

    def test_env_cannot_replace_the_band_mcp_token(self) -> None:
        with pytest.raises(ValueError, match=BAND_MCP_TOKEN_ENV):
            ClaudeCLIOptions(env={BAND_MCP_TOKEN_ENV: "x"})

    def test_extra_args_keys_are_bare_flag_names(self) -> None:
        with pytest.raises(ValueError, match="bare flag names"):
            ClaudeCLIOptions(extra_args={"--debug-to-stderr": None})
