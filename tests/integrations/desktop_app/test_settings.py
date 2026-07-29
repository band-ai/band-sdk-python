"""Where the room view gets its credentials and its tuning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from band.integrations.desktop_app.settings import (
    MAX_ROOM_EVENT_TIMEOUT_S,
    DesktopRoomViewSettings,
    RoomViewTuning,
    websocket_url,
)
from tests.integrations.desktop_app.conftest import message

PLATFORM = "https://platform.example"
STANDARD_WS = "wss://platform.example/api/v1/socket/websocket"


def agent_entry(agent: str) -> dict[str, Any]:
    """A Claude Desktop entry running band-mcp as one Band agent."""
    return {
        "command": "/Users/example/.local/bin/band-mcp",
        "args": ["--scope", "agent"],
        "env": {"BAND_AGENT_KEY": f"{agent}-key", "BAND_BASE_URL": PLATFORM},
    }


def desktop_config(directory: Path, **servers: dict[str, Any]) -> Path:
    """A claude_desktop_config.json holding the given mcpServers entries."""
    path = directory / "claude_desktop_config.json"
    path.write_text(json.dumps({"mcpServers": servers}))
    return path


class TestConnection:
    """Resolving the agent key, REST origin and WebSocket endpoint."""

    def test_explicit_environment_is_used_as_given(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAND_AGENT_KEY", "agent-key")
        monkeypatch.setenv("BAND_BASE_URL", PLATFORM)
        monkeypatch.delenv("BAND_WS_URL", raising=False)

        settings = DesktopRoomViewSettings()

        assert settings.resolve_connection() == ("agent-key", PLATFORM, STANDARD_WS)
        assert "agent-key" not in repr(settings), (
            "the key must stay a SecretStr so it cannot reach a log or traceback"
        )

    def test_the_desktop_band_mcp_entry_supplies_the_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The user configures the key once, for band-mcp; we reuse that."""
        monkeypatch.delenv("BAND_AGENT_KEY", raising=False)
        monkeypatch.delenv("BAND_WS_URL", raising=False)
        config = desktop_config(tmp_path, **{"custom-name": agent_entry("tom")})

        settings = DesktopRoomViewSettings(band_desktop_config=config)

        assert settings.resolve_connection() == ("tom-key", PLATFORM, STANDARD_WS)

    def test_a_runner_launching_band_mcp_is_recognised(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`uvx band-mcp` is the same agent entry, one word further along."""
        monkeypatch.delenv("BAND_AGENT_KEY", raising=False)
        monkeypatch.delenv("BAND_WS_URL", raising=False)

        settings = DesktopRoomViewSettings(
            band_desktop_config=desktop_config(
                tmp_path,
                band={
                    "command": "/Users/example/.local/bin/uvx",
                    "args": ["band-mcp", "--scope", "agent"],
                    "env": {"BAND_AGENT_KEY": "from-runner"},
                },
            )
        )

        assert settings.resolve_connection()[0] == "from-runner"

    def test_two_agents_are_refused_rather_than_guessed_between(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Config order deciding which agent Desktop *is* would be silent and
        wrong; the room view would hold the WebSocket as the other one."""
        monkeypatch.delenv("BAND_AGENT_KEY", raising=False)
        config = desktop_config(
            tmp_path, tom=agent_entry("tom"), jerry=agent_entry("jerry")
        )

        with pytest.raises(ValueError, match="BAND_DESKTOP_MCP_SERVER") as refusal:
            DesktopRoomViewSettings(band_desktop_config=config).resolve_connection()

        assert "jerry, tom" in str(refusal.value), "the user has to be told the options"
        assert (
            DesktopRoomViewSettings(
                band_desktop_config=config,
                band_desktop_mcp_server="jerry",
            ).resolve_connection()[0]
            == "jerry-key"
        )

    def test_naming_a_server_that_is_not_there_says_which_are(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BAND_AGENT_KEY", raising=False)

        with pytest.raises(ValueError, match="has: tom"):
            DesktopRoomViewSettings(
                band_desktop_config=desktop_config(tmp_path, tom=agent_entry("tom")),
                band_desktop_mcp_server="typo",
            ).resolve_connection()

    def test_an_explicit_websocket_url_wins(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("BAND_AGENT_KEY", "agent-key")
        monkeypatch.setenv("BAND_BASE_URL", PLATFORM)
        monkeypatch.setenv("BAND_WS_URL", "wss://socket.example/ws")

        assert DesktopRoomViewSettings().resolve_connection() == (
            "agent-key",
            PLATFORM,
            "wss://socket.example/ws",
        )

    def test_no_key_anywhere_says_how_to_supply_one(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("BAND_AGENT_KEY", raising=False)

        with pytest.raises(ValueError, match="BAND_DESKTOP_CONFIG"):
            DesktopRoomViewSettings(
                band_desktop_config=tmp_path / "missing.json"
            ).resolve_connection()

    def test_a_rest_url_with_no_scheme_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported BAND_BASE_URL"):
            websocket_url("platform.example")


class TestTuning:
    def test_the_monitor_block_is_overridable_and_bounded(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """This is the knob operators reach for: it sets the user's wait."""
        monkeypatch.setenv("BAND_ROOM_EVENT_TIMEOUT_S", "3")

        assert RoomViewTuning().band_room_event_timeout_s == 3

        monkeypatch.setenv(
            "BAND_ROOM_EVENT_TIMEOUT_S", str(MAX_ROOM_EVENT_TIMEOUT_S + 1)
        )
        with pytest.raises(ValidationError):
            RoomViewTuning()

    async def test_a_room_runs_on_the_tuning_it_was_given(self, room: Any) -> None:
        """Tuning is injected, so an install's settings reach the reads."""
        live = room(
            [
                message(f"m-{index}", f"2026-01-01T00:00:{index:02d}Z")
                for index in range(5)
            ],
            tuning=RoomViewTuning(band_initial_transcript_messages=2),
        )

        result = await live.read()

        assert len(result.messages) == 2
