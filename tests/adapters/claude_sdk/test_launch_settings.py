"""Every Claude CLI launch reads host settings layered under the adapter's isolation."""

from __future__ import annotations

import json
import logging
import os
import signal
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from band.adapters.claude_sdk import (
    BAND_MCP_TOKEN_ENV,
    ISOLATION_OVERRIDE_WARNING,
    ISOLATION_SETTINGS,
    LAUNCH_FILE_MISSING_WARNING,
    ClaudeCLIOptions,
    ClaudeSDKAdapter,
    ClaudeSDKAdapterConfig,
    LaunchSettings,
    SettingsRejection,
)
from band.core.exceptions import CleanupIncompleteError
from tests.adapters.claude_sdk.fakecli import Hold
from tests.adapters.claude_sdk.helpers import (
    AGENT_DESCRIPTION,
    AGENT_NAME,
    REPORTING_BUDGET,
    OpenRoom,
    band_mcp_running,
    launch_options,
    sdk_cli_argv,
)

ADAPTER_LOGGER = ClaudeSDKAdapter.__module__
HOST_SETTINGS = {"permissions": {"defaultMode": "plan"}, "model": "sonnet"}
SECRET = "sk-host-secret"
HostSource = Callable[[dict[str, Any], Path], str]


def _as_file(settings: dict[str, Any], tmp_path: Path) -> str:
    path = tmp_path / "team-settings.json"
    path.write_text(json.dumps(settings))
    return str(path)


def _as_inline(settings: dict[str, Any], tmp_path: Path) -> str:
    return json.dumps(settings)


HOST_SOURCES = pytest.mark.parametrize(
    "source", [_as_file, _as_inline], ids=["file", "inline"]
)


def _settings_argument(options: ClaudeAgentOptions) -> str:
    """The single ``--settings`` value the CLI is launched with."""
    argv = sdk_cli_argv(options)
    assert argv.count("--settings") == 1
    return argv[argv.index("--settings") + 1]


def _launch_file(options: ClaudeAgentOptions) -> Path:
    return Path(_settings_argument(options))


def _launch_settings(options: ClaudeAgentOptions) -> dict[str, Any]:
    """The settings the CLI loads from its ``--settings`` file."""
    return json.loads(_launch_file(options).read_text(encoding="utf-8"))


def _adapter_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == ADAPTER_LOGGER and record.levelno >= logging.WARNING
    ]


@contextmanager
def _file_size_limit(size: int) -> Iterator[None]:
    """Make the kernel fail writes past ``size`` bytes, as a full disk would."""
    import resource  # noqa: PLC0415 -- POSIX-only; the test skips on Windows

    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    previous = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, hard))
    try:
        yield
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
        signal.signal(signal.SIGXFSZ, previous)


def _with_host(settings: str) -> ClaudeSDKAdapterConfig:
    return ClaudeSDKAdapterConfig(cli=ClaudeCLIOptions(settings=settings))


class TestLaunchSettings:
    async def test_an_agent_without_host_settings_launches_isolated_inline(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(ClaudeSDKAdapterConfig())

        options = await launch_options(room)

        assert json.loads(_settings_argument(options)) == ISOLATION_SETTINGS

    @HOST_SOURCES
    async def test_host_settings_are_layered_under_the_isolation_keys(
        self, claude_room: OpenRoom, tmp_path: Path, source: HostSource
    ) -> None:
        room = await claude_room(_with_host(source(HOST_SETTINGS, tmp_path)))

        options = await launch_options(room)

        assert _launch_settings(options) == {**HOST_SETTINGS, **ISOLATION_SETTINGS}

    @pytest.mark.parametrize(
        ("overrides", "warnings"),
        [
            (
                {"crossSessionInbound": "allow"},
                [ISOLATION_OVERRIDE_WARNING % (["crossSessionInbound"],)],
            ),
            (
                {"disableClaudeAiConnectors": False},
                [ISOLATION_OVERRIDE_WARNING % (["disableClaudeAiConnectors"],)],
            ),
            (
                {"crossSessionInbound": "allow", "disableClaudeAiConnectors": False},
                [
                    ISOLATION_OVERRIDE_WARNING
                    % (["crossSessionInbound", "disableClaudeAiConnectors"],)
                ],
            ),
            (dict(ISOLATION_SETTINGS), []),
        ],
        ids=["inbound-conflict", "connectors-conflict", "both-conflict", "matching"],
    )
    async def test_the_isolation_keys_win_over_the_host(
        self,
        claude_room: OpenRoom,
        caplog: pytest.LogCaptureFixture,
        overrides: dict[str, Any],
        warnings: list[str],
    ) -> None:
        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            room = await claude_room(
                _with_host(json.dumps({**HOST_SETTINGS, **overrides}))
            )
        logged = _adapter_warnings(caplog)

        options = await launch_options(room)

        assert _launch_settings(options) == {**HOST_SETTINGS, **ISOLATION_SETTINGS}
        assert logged == warnings

    @HOST_SOURCES
    async def test_host_settings_never_reach_the_cli_argv(
        self, claude_room: OpenRoom, tmp_path: Path, source: HostSource
    ) -> None:
        secret_settings = {"env": {"ANTHROPIC_API_KEY": SECRET}}
        room = await claude_room(_with_host(source(secret_settings, tmp_path)))

        options = await launch_options(room)

        assert SECRET not in " ".join(sdk_cli_argv(options))
        assert _launch_settings(options)["env"] == secret_settings["env"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
    async def test_only_the_owner_can_read_the_launch_file(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(_with_host(json.dumps(HOST_SETTINGS)))

        options = await launch_options(room)

        assert stat.S_IMODE(_launch_file(options).stat().st_mode) == 0o600

    async def test_launches_share_one_launch_file_until_the_adapter_stops(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(_with_host(json.dumps(HOST_SETTINGS)))
        first = _launch_file(await launch_options(room))
        second = _launch_file(await launch_options(room.beside("room-2")))

        await room.adapter.cleanup_all()

        assert second == first
        assert not first.exists()

    async def test_a_launch_rewrites_a_launch_file_removed_under_it(
        self, claude_room: OpenRoom, caplog: pytest.LogCaptureFixture
    ) -> None:
        room = await claude_room(_with_host(json.dumps(HOST_SETTINGS)))
        removed = _launch_file(await launch_options(room))
        removed.unlink()

        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            later = await launch_options(room.beside("room-2"))
        logged = _adapter_warnings(caplog)

        assert _launch_settings(later) == {**HOST_SETTINGS, **ISOLATION_SETTINGS}
        assert logged == [LAUNCH_FILE_MISSING_WARNING % removed]

    async def test_a_restart_rereads_the_host_file_and_retires_the_old_launch_file(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        host_file = tmp_path / "team-settings.json"
        host_file.write_text(json.dumps(HOST_SETTINGS))
        room = await claude_room(_with_host(str(host_file)))
        first_file = _launch_file(await launch_options(room))
        edited = {**HOST_SETTINGS, "model": "opus"}
        host_file.write_text(json.dumps(edited))

        await room.restart()
        after_restart = await launch_options(room.beside("room-2"))

        assert not first_file.exists()
        assert _launch_settings(after_restart) == {**edited, **ISOLATION_SETTINGS}

    @pytest.mark.looptime
    async def test_a_failed_stop_keeps_the_launch_file_for_its_retry(
        self, claude_room: OpenRoom
    ) -> None:
        room = await claude_room(
            ClaudeSDKAdapterConfig(
                retention=REPORTING_BUDGET,
                cli=ClaudeCLIOptions(settings=json.dumps(HOST_SETTINGS)),
            )
        )
        launch_file = _launch_file(await launch_options(room))
        closing = room.claude.closing = Hold()

        with pytest.raises(CleanupIncompleteError):
            await room.adapter.cleanup_all()
        kept = launch_file.exists()
        closing.released.set()
        await room.adapter.cleanup_all()

        assert (kept, launch_file.exists()) == (True, False)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file-size limit")
    def test_a_failed_write_leaves_no_partial_launch_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        settings = LaunchSettings(HOST_SETTINGS)

        with _file_size_limit(8), pytest.raises(OSError):
            settings.for_launch()

        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            (None, SettingsRejection.UNREADABLE),
            ('{"model": "caf\u00e9"}'.encode("cp1252"), SettingsRejection.NOT_UTF8),
            (b"{not json", SettingsRejection.NOT_JSON),
            (b'{"cleanupPeriodDays": NaN}', SettingsRejection.NOT_JSON),
            (b'{"cleanupPeriodDays": 1e400}', SettingsRejection.NOT_JSON),
            (b"[]", SettingsRejection.NOT_OBJECT),
            (
                json.dumps({"env": {BAND_MCP_TOKEN_ENV: "x"}}).encode(),
                SettingsRejection.BAND_TOKEN,
            ),
        ],
        ids=[
            "missing",
            "not-utf8",
            "malformed",
            "non-finite",
            "overflow",
            "not-an-object",
            "band-token",
        ],
    )
    async def test_an_unusable_settings_file_fails_the_start_before_band_mcp_opens(
        self, tmp_path: Path, content: bytes | None, reason: SettingsRejection
    ) -> None:
        settings = tmp_path / "team-settings.json"
        if content is not None:
            settings.write_bytes(content)
        adapter = ClaudeSDKAdapter(_with_host(str(settings)))

        try:
            with pytest.raises(ValueError, match=reason):
                await adapter.on_started(AGENT_NAME, AGENT_DESCRIPTION)
            assert not band_mcp_running(adapter)
        finally:
            await adapter.cleanup_all()

    @pytest.mark.parametrize(
        ("settings", "reason"),
        [
            ("{not json", SettingsRejection.NOT_JSON),
            ('{"cleanupPeriodDays": Infinity}', SettingsRejection.NOT_JSON),
            ('{"cleanupPeriodDays": -1e400}', SettingsRejection.NOT_JSON),
            (
                json.dumps({"env": {BAND_MCP_TOKEN_ENV: "x"}}),
                SettingsRejection.BAND_TOKEN,
            ),
            ("team-settings.json", SettingsRejection.NOT_INLINE_OR_ABSOLUTE),
        ],
        ids=[
            "malformed-inline",
            "non-finite-inline",
            "overflow-inline",
            "band-token",
            "relative-path",
        ],
    )
    def test_settings_the_cli_could_not_load_are_rejected_on_construction(
        self, settings: str, reason: SettingsRejection
    ) -> None:
        with pytest.raises(ValueError, match=reason):
            ClaudeCLIOptions(settings=settings)

    def test_inline_settings_may_start_with_whitespace(self) -> None:
        options = ClaudeCLIOptions(settings=f"\n  {json.dumps(HOST_SETTINGS)}")

        assert options.host_settings() == HOST_SETTINGS

    @pytest.mark.parametrize(
        "build",
        [
            lambda: ClaudeCLIOptions(settings=f'{{"env": {{"KEY": "{SECRET}"}}'),
            lambda: ClaudeSDKAdapterConfig(
                cli={"settings": f'{{"env": {{"KEY": "{SECRET}"}}'}
            ),
        ],
        ids=["settings", "settings-in-adapter-config"],
    )
    def test_a_rejected_secret_is_not_echoed(self, build: Callable[[], object]) -> None:
        with pytest.raises(ValueError) as rejected:
            build()

        assert SECRET not in str(rejected.value)

    async def test_settings_survive_fresh_resume_and_the_fresh_replacement(
        self, claude_room: OpenRoom, tmp_path: Path
    ) -> None:
        # options_for() builds every room session's launch, so the settings must
        # ride every way a session begins: a fresh room, the bootstrap resume of a
        # recorded session, and the fresh session that heals an unresumable one.
        config = _with_host(_as_file(HOST_SETTINGS, tmp_path))
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
        assert [_launch_settings(session.options) for session in launches] == [
            {**HOST_SETTINGS, **ISOLATION_SETTINGS}
        ] * len(launches)
        assert first.chat + second.chat + third.chat == ["first", "resumed", "replaced"]
