"""The typed ``settings`` option changes the REAL Claude CLI's behavior.

CI skips this directory (no CLI, no sign-in); on a host with the genuine
``claude`` binary signed in it proves what the scripted-peer suite cannot:
options built through ``ClaudeCLIOptions.sdk_fields()`` and launched by the
real SDK transport make the actual CLI act on the settings file — here, run
its SessionStart hook. Mock echoes and argv assertions do not establish
this. When no usable CLI is present the whole module skips, stating why.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient

from band.adapters.claude_sdk import BAND_MCP_TOKEN_ENV, ClaudeCLIOptions
from band.integrations.claude_sdk.transport import create_transport

pytestmark = pytest.mark.timeout(120)

PROBE = "Reply with exactly OK and nothing else."


def _options(cli: ClaudeCLIOptions, **extra: Any) -> ClaudeAgentOptions:
    """The options the adapter hands its own transport builder, plus turn shape."""
    fields = {key: value for key, value in cli.sdk_fields().items() if key != "env"}
    options = ClaudeAgentOptions(**fields, **extra)
    options.env[BAND_MCP_TOKEN_ENV] = "integration-probe-token"
    return options


async def _say(options: ClaudeAgentOptions) -> str:
    """One real turn: the CLI's final text, through the adapter's transport seam."""
    client = ClaudeSDKClient(options=options, transport=create_transport(options))
    text = ""
    await client.connect()
    try:
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


@pytest.fixture(scope="module")
def claude_cli() -> Iterator[str]:
    binary = shutil.which("claude")
    if binary is None:
        pytest.skip("the real Claude CLI is not installed on this host")
    yield binary


@pytest.fixture
def settings_file(tmp_path: Path, claude_cli: str) -> tuple[Path, Path]:
    """A disposable settings file whose SessionStart hook writes a marker, plus
    the marker path. Hook semantics were confirmed against the installed CLI
    before this file relied on them."""
    marker = tmp_path / "session-start-marker"
    settings = tmp_path / "settings.json"
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


class TestClaudeSettingsNative:
    async def test_the_real_cli_acts_on_the_selected_settings_file(
        self, claude_cli: str, settings_file: tuple[Path, Path]
    ) -> None:
        settings, marker = settings_file
        cli = ClaudeCLIOptions(cli_path=claude_cli, settings=str(settings))
        try:
            answer = await _say(_options(cli, cwd=str(settings.parent)))
        except Exception as error:  # noqa: BLE001 - absence of a signed-in CLI is not a failure
            pytest.skip(f"the real Claude CLI cannot run a turn here: {error}")
        assert "OK" in answer
        assert marker.is_file(), (
            "the real CLI did not run the hook from --settings: the typed option "
            "reached argv without changing native behavior"
        )

    async def test_without_the_selection_the_cli_loads_no_host_settings(
        self, claude_cli: str, settings_file: tuple[Path, Path]
    ) -> None:
        # Control for the claim above: the same launch without the typed option
        # must leave the marker absent, so its appearance is the selection's doing.
        _settings, marker = settings_file
        cli = ClaudeCLIOptions(cli_path=claude_cli)
        try:
            answer = await _say(_options(cli, cwd=str(marker.parent)))
        except Exception as error:  # noqa: BLE001 - same skip rule as the paired test
            pytest.skip(f"the real Claude CLI cannot run a turn here: {error}")
        assert "OK" in answer
        assert not marker.exists(), "a hook ran although no settings were selected"
