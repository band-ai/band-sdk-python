"""Configuration for the Desktop room view: tuning knobs and credentials."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from typing import Any

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The ceiling the monitor schema advertises, so a caller cannot ask us to block
# for an unreasonable time. Also the worst case before a typed message is seen.
MAX_ROOM_EVENT_TIMEOUT_S = 30

# The executable that makes a Claude Desktop MCP entry a Band agent, whether it
# is named directly or handed to a runner such as uvx.
BAND_MCP_COMMAND = "band-mcp"


def runs_band_mcp(server: dict[str, Any]) -> bool:
    """Whether a Desktop MCP entry launches band-mcp, directly or via a runner."""
    launched = [server.get("command"), *(server.get("args") or [])]
    return any(Path(str(word)).name == BAND_MCP_COMMAND for word in launched if word)


class RoomViewTuning(BaseSettings):
    """Per-install knobs for the Desktop room view.

    Read once at import because the tool schemas bake these in as defaults and
    bounds, which the host reads at connect time.
    """

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    band_room_event_timeout_s: int = Field(
        10,
        ge=1,
        le=MAX_ROOM_EVENT_TIMEOUT_S,
        description=(
            "How long the monitor blocks. Also the worst-case delay before the "
            "agent notices the user typed, since the host queues that message "
            "behind the in-flight call."
        ),
    )
    band_transcript_page_size: int = Field(100, ge=1)
    band_initial_transcript_messages: int = Field(25, ge=1)
    band_max_message_chars: int = Field(2_000, ge=1)


def websocket_url(rest_url: str) -> str:
    """Derive the platform WebSocket endpoint from its REST origin."""
    parsed = urlsplit(rest_url)
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme)
    if scheme is None or not parsed.netloc:
        raise ValueError(f"Unsupported BAND_BASE_URL: {rest_url}")
    return urlunsplit((scheme, parsed.netloc, "/api/v1/socket/websocket", "", ""))


class DesktopRoomViewSettings(BaseSettings):
    """Credentials for the Desktop-owned local transcript process."""

    model_config = SettingsConfigDict(
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
        populate_by_name=True,
    )

    band_agent_key: SecretStr = SecretStr("")
    band_base_url: str = Field(
        "https://app.band.ai",
        validation_alias=AliasChoices("BAND_BASE_URL", "BAND_REST_URL"),
    )
    band_ws_url: str = Field("", validation_alias="BAND_WS_URL")
    band_desktop_mcp_server: str = Field(
        "",
        description=(
            "Which Claude Desktop mcpServers entry to take the agent identity "
            "from. Only needed when more than one of them runs band-mcp."
        ),
    )
    band_desktop_config: Path = Field(
        default_factory=lambda: (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    )

    def agent_servers(self) -> dict[str, dict[str, str]]:
        """The env of every Desktop MCP entry that runs band-mcp as an agent."""
        try:
            config = json.loads(self.band_desktop_config.expanduser().read_text())
        except (OSError, json.JSONDecodeError):
            return {}
        return {
            name: server["env"]
            for name, server in (config.get("mcpServers") or {}).items()
            if runs_band_mcp(server) and (server.get("env") or {}).get("BAND_AGENT_KEY")
        }

    def resolve_connection(self) -> tuple[str, str, str]:
        """Use an explicit key, else borrow the one Desktop gives band-mcp.

        Borrowing is what keeps the agent key out of a second config entry, so
        which entry it is borrowed from has to be unambiguous: on a machine
        running two Band agents, JSON ordering must not get to decide which of
        them Claude Desktop is.
        """
        if key := self.band_agent_key.get_secret_value():
            return self._connection(key, self.band_base_url, self.band_ws_url)

        servers = self.agent_servers()
        chosen = self.band_desktop_mcp_server
        if not servers:
            raise ValueError(
                "BAND_AGENT_KEY is required for the Desktop room view. No MCP "
                f"server in {self.band_desktop_config} runs band-mcp with a "
                "BAND_AGENT_KEY in its env. Configure an agent-scope band-mcp "
                "server in Claude Desktop, set BAND_AGENT_KEY on the room view, "
                "or point BAND_DESKTOP_CONFIG at the right config file."
            )
        named = ", ".join(sorted(servers))
        if chosen and chosen not in servers:
            raise ValueError(
                f"Claude Desktop has no agent band-mcp server named '{chosen}'. "
                f"It has: {named}."
            )
        if not chosen and len(servers) > 1:
            raise ValueError(
                f"Claude Desktop runs band-mcp as more than one Band agent "
                f"({named}), so the room view cannot tell which agent it is. "
                "Set BAND_DESKTOP_MCP_SERVER to one of those names on the "
                "band-room-view entry."
            )

        environment = servers[chosen or next(iter(servers))]
        return self._connection(
            str(environment["BAND_AGENT_KEY"]),
            str(
                environment.get("BAND_BASE_URL")
                or environment.get("BAND_REST_URL")
                or self.band_base_url
            ),
            str(environment.get("BAND_WS_URL") or self.band_ws_url),
        )

    def _connection(self, key: str, rest_url: str, ws_url: str) -> tuple[str, str, str]:
        return key, rest_url, ws_url or websocket_url(rest_url)
