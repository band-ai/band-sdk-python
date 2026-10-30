"""The typed ``settings`` option makes the REAL Claude CLI act on the selection.

CI skips this directory (no CLI, no sign-in); on a host with the genuine ``claude``
binary signed in it proves what the scripted-peer suite cannot: options built through
``ClaudeCLIOptions.sdk_fields()`` the way ``ClaudeSDKAdapter`` builds them make the
actual CLI act on the selected settings file — here, run its SessionStart hook. Mock
echoes and argv assertions do not establish this.

Prerequisites are admitted up front and explicitly: the binary must exist, and for the
behavioral pair one probe turn must succeed. Anything that fails after admission — a
settings regression, a transport failure, a cleanup error — fails the test; only a
missing prerequisite skips, naming what is missing.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from band.adapters.claude_sdk import (
    BAND_MCP_TOKEN_ENV,
    ClaudeCLIOptions,
    ClaudeSDKAdapterConfig,
)
from band.core.defaultmodels import ANTHROPIC_MODEL
from band.integrations.claude_sdk.transport import create_transport

pytestmark = pytest.mark.timeout(120)

PROBE = "Reply with exactly OK and nothing else."
HANG_S = 10.0  # bounds a deliberately non-handshaking child; never drives a scenario


def production_options(cli: ClaudeCLIOptions) -> ClaudeAgentOptions:
    """The options ``ClaudeSDKAdapter`` hands its transport, for a probe with no Band
    server attached: same pinned model, same permission mode, same closed
    ``setting_sources``, same env composition (the adapter always injects the MCP
    token variable). A probe that diverged on any of these would not be showing what
    a production launch does."""
    config = ClaudeSDKAdapterConfig(cli=cli)
    options = ClaudeAgentOptions(
        model=config.model or ANTHROPIC_MODEL,
        permission_mode=config.permission_mode,  # type: ignore[arg-type]
        setting_sources=list(config.setting_sources),  # type: ignore[arg-type]
        **cli.sdk_fields(),
    )
    options.env[BAND_MCP_TOKEN_ENV] = "integration-probe-token"
    return options


async def _say(options: ClaudeAgentOptions) -> str:
    """One real turn through the adapter's transport seam. ``connect()`` is inside the
    try: a child spawned by a failing or cancelled connect must still be reaped by
    ``disconnect()`` — that cleanup is part of what these cells prove."""
    client = ClaudeSDKClient(options=options, transport=create_transport(options))
    text = ""
    try:
        await client.connect()
        await client.query(PROBE)
        async for message in client.receive_response():
            content = getattr(message, "content", None)
            if isinstance(content, str):
                text += content
            elif isinstance(content, list):
                text += "".join(
                    block.text for block in content if getattr(block, "text", None)
                )
    finally:
        await client.disconnect()
    return text


async def _assert_dead(pid: int, within_s: float = 10.0) -> None:
    """Poll the spawned pid until it is gone: a leaked test child fails the cell."""
    deadline = asyncio.get_running_loop().time() + within_s
    while asyncio.get_running_loop().time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"child {pid} outlived the failed connect")


@pytest.fixture(scope="module")
def claude_binary() -> Iterator[str]:
    binary = shutil.which("claude")
    if binary is None:
        pytest.skip("the real Claude CLI is not installed on this host")
    yield binary


@pytest.fixture(scope="module")
def signed_in_claude(claude_binary: str) -> Iterator[str]:
    """Admission for the behavioral pair: one probe turn must work, or the host is
    simply not signed in — a missing prerequisite, stated as the skip reason. After
    this admission, the tests themselves fail on any product error."""
    try:
        answer = asyncio.run(
            _say(production_options(ClaudeCLIOptions(cli_path=claude_binary)))
        )
    except Exception as error:  # noqa: BLE001 - admission boundary: anything failing here is "not signed in"
        pytest.skip(f"the real Claude CLI cannot run a probe turn here: {error}")
    assert "OK" in answer
    yield claude_binary


def _hook_settings(tmp_dir: Path) -> tuple[Path, Path]:
    """A settings file whose SessionStart hook touches a marker, plus the path. Hook
    semantics were confirmed against the installed CLI before these tests relied on
    them (the same shape the band-agent plan's smoke protocol prescribed)."""
    marker = tmp_dir / "session-start-marker"
    settings = tmp_dir / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "startup",
                            "hooks": [
                                {"type": "command", "command": f"touch {marker}"}
                            ],
                        }
                    ]
                }
            }
        )
    )
    return settings, marker


def _cli_script(tmp_dir: Path, *, body: str) -> Path:
    """A stand-in CLI: a real child process running ``body`` (records its pid, then
    misbehaves the way the scenario needs)."""
    script = tmp_dir / "fake-cli.py"
    script.write_text(
        f"#!{sys.executable}\n"
        "import os, pathlib, sys, time\n"
        f"pathlib.Path({str(tmp_dir / 'child.pid')!r}).write_text(str(os.getpid()))\n"
        + body
    )
    script.chmod(0o755)
    return script


class TestClaudeSettingsNative:
    async def test_the_real_cli_acts_on_the_selected_settings_file(
        self, signed_in_claude: str, tmp_path: Path
    ) -> None:
        # Post-admission there is no skip path: a regression in the option is a failure.
        settings, marker = _hook_settings(tmp_path)
        cli = ClaudeCLIOptions(cli_path=signed_in_claude, settings=str(settings))
        answer = await _say(production_options(cli))
        assert "OK" in answer
        assert marker.is_file(), (
            "the real CLI did not run the hook from --settings: the typed option "
            "reached argv without changing native behavior"
        )

    async def test_without_the_selection_the_same_hook_never_runs(
        self, signed_in_claude: str, tmp_path: Path
    ) -> None:
        # Control for the claim above, identical options minus the selection: the hook
        # is the selection's doing. (This shows the FILE hook only; inline-JSON and
        # permission-routing semantics stay proven at the scripted level.)
        _settings, marker = _hook_settings(tmp_path)
        cli = ClaudeCLIOptions(cli_path=signed_in_claude)
        answer = await _say(production_options(cli))
        assert "OK" in answer
        assert not marker.exists(), "the hook ran although no settings were selected"

    async def test_a_connect_failure_after_spawn_leaves_no_child(
        self, claude_binary: str, tmp_path: Path
    ) -> None:
        # A CLI that dies before completing the handshake fails connect() — after the
        # child was already spawned. The cleanup that must cover it is _say's finally.
        script = _cli_script(tmp_path, body="sys.exit(1)\n")
        cli = ClaudeCLIOptions(cli_path=str(script))
        with pytest.raises(Exception):  # noqa: B017 - any connect-time failure will do
            await _say(production_options(cli))
        pid = int((tmp_path / "child.pid").read_text())
        await _assert_dead(pid)

    async def test_cancellation_during_connect_ends_the_spawned_child(
        self, claude_binary: str, tmp_path: Path
    ) -> None:
        # A CLI that never handshakes parks connect(); the cancellation arriving there
        # (what the adapter's startup timeout does to a wedged CLI) must still reap it.
        script = _cli_script(tmp_path, body="time.sleep(600)\n")
        cli = ClaudeCLIOptions(cli_path=str(script))
        with pytest.raises((TimeoutError, asyncio.CancelledError)):
            await asyncio.wait_for(_say(production_options(cli)), HANG_S)
        pid = int((tmp_path / "child.pid").read_text())
        await _assert_dead(pid)
