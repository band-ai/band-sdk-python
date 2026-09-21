# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]>=1.2.0"]
# ///
"""
Cursor ACP Client - Use Cursor's AI agent from Band.

Spawns Cursor's CLI agent (`agent acp`) as a subprocess and bridges it
to the Band platform. Messages from Band rooms are forwarded to Cursor,
and Cursor's responses (including tool calls, plans, and streaming text) are
posted back to the room.

Note: Cursor IDE does NOT yet support connecting to remote ACP agents (i.e.,
you cannot add Band as an agent inside Cursor's UI). This integration works
the other direction — Band spawns Cursor's agent as a backend.

For the reverse direction (IDE connects to Band), see:
- JetBrains: examples/acp/servers/jetbrains.py
- Zed: examples/acp/servers/basic.py
- Any ACP client: band-acp CLI

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter
        -> Cursor ACP subprocess
          -> Cursor CLI Agent (with Band MCP tools injected)
            -> session_update responses streamed back
        -> Posts response to Band room

Prerequisites:
    1. Cursor CLI installed and authenticated:
       agent login
       # OR set CURSOR_API_KEY / CURSOR_AUTH_TOKEN environment variable

    2. Set environment variables:
       - BAND_API_KEY: Your Band API key (required for tool injection)

    3. Optionally configure:
       - CURSOR_API_KEY: Cursor API key (alternative to `agent login`)
       - ACP_AGENT_CWD: Working directory for Cursor sessions (default: .)
       - CURSOR_MODEL: Select a model if Cursor advertises it
       - CURSOR_REASONING_EFFORT: Select a reasoning effort if Cursor advertises it

Run with:
    uv run examples/acp/clients/cursor.py
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from functools import partial

from acp.schema import SessionConfigOptionSelect
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import ACPConfigRequest, CursorACPAdapter, CursorACPAdapterConfig
from band.integrations.acp.session_config import flatten_select_options

configure_logging(
    level=logging.INFO,
    root_level=logging.INFO,
    extra_loggers={
        "httpcore": logging.WARNING,
        "httpx": logging.WARNING,
    },
)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    acp_agent_cwd: str = "."
    cursor_api_key: str = ""
    cursor_auth_token: str = ""
    cursor_command: str = "agent acp"
    cursor_model: str = ""
    cursor_reasoning_effort: str = ""

    @property
    def session_config_preferences(self) -> dict[str, str]:
        """Return configured Cursor values, omitting unset preferences."""
        return {
            option_id: value
            for option_id, value in (
                ("model", self.cursor_model),
                ("reasoning_effort", self.cursor_reasoning_effort),
            )
            if value
        }


def advertised_config_values(request: ACPConfigRequest) -> dict[str, tuple[str, ...]]:
    """Project Cursor's live select catalog into option ids and values."""
    return {
        option.id: tuple(
            entry.value for entry in flatten_select_options(option.options)
        )
        for option in request.config_options
        if isinstance(option, SessionConfigOptionSelect)
    }


async def choose_session_config(
    request: ACPConfigRequest,
    *,
    preferences: Mapping[str, str],
) -> dict[str, str]:
    """Select configured values only when Cursor advertises them."""
    catalog = advertised_config_values(request)
    logger.info("Cursor session '%s' config options: %s", request.session_id, catalog)
    selected: dict[str, str] = {}
    for option_id, selected_value in preferences.items():
        values = catalog.get(option_id)
        if values is None:
            logger.warning(
                "Cursor session '%s' does not advertise config option '%s'.",
                request.session_id,
                option_id,
            )
        elif selected_value in values:
            selected[option_id] = selected_value
        else:
            logger.warning(
                "Cursor session '%s' does not offer '%s' for '%s'.",
                request.session_id,
                selected_value,
                option_id,
            )
    return selected


async def main() -> None:
    load_dotenv()
    settings = Settings()
    cwd = settings.acp_agent_cwd

    adapter = CursorACPAdapter(
        CursorACPAdapterConfig(
            command=tuple(settings.cursor_command.split()),
            cwd=cwd,
            api_key=settings.cursor_api_key or None,
            auth_token=settings.cursor_auth_token or None,
            resolve_session_config=partial(
                choose_session_config,
                preferences=settings.session_config_preferences,
            ),
        )
    )

    logger.info("Starting Cursor ACP client bridge...")
    logger.info("Messages from Band will be forwarded to Cursor's agent.")
    async with Agent.from_config(
        "cursor_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
