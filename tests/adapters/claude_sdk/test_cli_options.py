"""CLI launch options a host sets on the adapter reach the Claude CLI."""

from __future__ import annotations

import json
import logging
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
    ISOLATION_SETTINGS,
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

ADAPTER_LOGGER = "band.adapters.claude_sdk"
HOST_SETTINGS = {"permissions": {"defaultMode": "plan"}, "model": "sonnet"}


async def _cli_options(
    room: ClaudeRoom, session_id: str | None = None
) -> ClaudeAgentOptions:
    """The options the room's CLI process was started with."""
    room.claude.script([room.model_reply("Hello.")])
    await room.send("hi", session_id=session_id)
    [session] = room.claude.sessions
    return session.options


def _cli_settings(options: ClaudeAgentOptions) -> dict[str, Any]:
    """The single ``--settings`` value the CLI is launched with, parsed."""
    argv = sdk_cli_argv(options)
    assert argv.count("--settings") == 1
    return json.loads(argv[argv.index("--settings") + 1])


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

    async def test_sdk_owned_flags_match_what_the_adapter_makes_the_sdk_emit(
        self, claude_room: OpenRoom, tmp_path: Path
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

    async def test_an_agent_without_settings_still_launches_isolated(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(ClaudeSDKAdapterConfig())

        options = await _cli_options(room)

        assert _cli_settings(options) == ISOLATION_SETTINGS

    async def test_a_settings_file_is_layered_under_the_isolation_keys(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        settings = tmp_path / "team-settings.json"
        settings.write_text(json.dumps(HOST_SETTINGS))
        room = await claude_room(
            ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(settings=str(settings)))
        )

        options = await _cli_options(room)

        assert _cli_settings(options) == {**HOST_SETTINGS, **ISOLATION_SETTINGS}

    async def test_inline_settings_are_layered_under_the_isolation_keys(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(
            ClaudeSDKAdapterConfig(
                cli=ClaudeCLIOptions(settings=json.dumps(HOST_SETTINGS))
            )
        )

        options = await _cli_options(room)

        assert _cli_settings(options) == {**HOST_SETTINGS, **ISOLATION_SETTINGS}

    async def test_the_isolation_keys_win_over_the_host_and_say_so(
        self, claude_room: OpenRoom, caplog: pytest.LogCaptureFixture
    ) -> None:
        reopened = {**HOST_SETTINGS, "crossSessionInbound": "allow"}
        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            room = await claude_room(
                ClaudeSDKAdapterConfig(
                    cli=ClaudeCLIOptions(settings=json.dumps(reopened))
                )
            )

        options = await _cli_options(room)

        assert _cli_settings(options) == {**HOST_SETTINGS, **ISOLATION_SETTINGS}
        assert [(r.levelno, r.args) for r in caplog.records] == [
            (logging.WARNING, (["crossSessionInbound"],))
        ]

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            (None, "cannot read the Claude settings file"),
            ("{not json", "is not valid JSON"),
            ("[]", "must be a JSON object"),
        ],
        ids=["missing", "malformed", "not-an-object"],
    )
    async def test_an_unusable_settings_file_fails_the_start(
        self,
        claude_room: OpenRoom,
        tmp_path: Path,
        content: str | None,
        reason: str,
    ) -> None:
        settings = tmp_path / "team-settings.json"
        if content is not None:
            settings.write_text(content)
        config = ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(settings=str(settings)))

        with pytest.raises(ValueError, match=reason):
            await claude_room(config)

    @pytest.mark.parametrize(
        ("settings", "reason"),
        [
            ("{not json", "is not valid JSON"),
            ("team-settings.json", "inline JSON or an absolute file path"),
        ],
        ids=["malformed-inline", "relative-path"],
    )
    def test_settings_the_cli_could_not_load_are_rejected_on_construction(
        self, settings: str, reason: str
    ) -> None:
        with pytest.raises(ValueError, match=reason):
            ClaudeCLIOptions(settings=settings)

    async def test_settings_survive_fresh_resume_and_the_fresh_replacement(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        # options_for() copies base_options per room session, so the settings must
        # ride every way a session begins: a fresh room, the bootstrap resume of a
        # recorded session, and the fresh session that heals an unresumable one.
        settings = tmp_path / "team-settings.json"
        settings.write_text(json.dumps(HOST_SETTINGS))
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
        assert [_cli_settings(session.options) for session in launches] == [
            {**HOST_SETTINGS, **ISOLATION_SETTINGS}
        ] * len(launches)
        assert first.chat + second.chat + third.chat == ["first", "resumed", "replaced"]

    async def test_settings_leave_band_wiring_and_permission_routing_in_place(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        settings = tmp_path / "team-settings.json"
        settings.write_text(json.dumps(HOST_SETTINGS))
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
