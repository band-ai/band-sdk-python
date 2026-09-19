# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[acp]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
ACP Client example - Use a remote ACP agent from Band.

This example connects to a remote ACP-compliant agent (Codex CLI, Gemini CLI,
Claude Code, Goose, etc.) and makes it available as a Band platform agent.
Messages from the platform are forwarded to the ACP agent, and responses are
posted back to the chat.

Architecture:
    Band Platform (message arrives in room)
      -> ACPClientAdapter
        -> remote ACP agent subprocess/session
          -> Remote ACP Agent (Codex CLI, Gemini CLI, etc.)
            -> session_update responses streamed back
        -> Posts response to Band room

Prerequisites:
    1. Set environment variables:
       - BAND_WS_URL: WebSocket URL
       - BAND_REST_URL: REST API URL
       - ACP_AGENT_COMMAND: Command to spawn the ACP agent
         (default: "npx @zed-industries/codex-acp")
       - ACP_MODEL: An advertised model id to select for each new session
       - ACP_REASONING_EFFORT: An advertised reasoning effort to select when
         ACP_MODEL is unset

    2. Have the remote ACP agent installed and available in PATH

    Leave ACP_MODEL and ACP_REASONING_EFFORT unset for the first run. The
    bridge logs the model, reasoning, and provider-specific select values that
    the remote ACP agent offers for each session.

Run with:
    uv run examples/acp/clients/generic.py
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from collections.abc import Mapping
from functools import partial

from acp.schema import SessionConfigOptionSelect
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import ACPConfigRequest, ACPClientAdapter
from band.config import load_agent_config
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

    acp_agent_command: str = "npx @zed-industries/codex-acp"
    acp_agent_cwd: str = "."
    acp_model: str = ""
    acp_reasoning_effort: str = ""

    @property
    def session_config_preferences(self) -> dict[str, str]:
        """Return configured ACP preferences, omitting unset values."""
        return {
            option_id: value
            for option_id, value in (
                ("model", self.acp_model),
                ("reasoning_effort", self.acp_reasoning_effort),
            )
            if value
        }


def advertised_config_values(request: ACPConfigRequest) -> dict[str, tuple[str, ...]]:
    """Project an ACP session's select catalog into option ids and values."""
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
    """Choose the first configured value that this session advertises."""
    catalog = advertised_config_values(request)
    logger.info("ACP session '%s' config options: %s", request.session_id, catalog)

    for option_id, selected_value in preferences.items():
        if selected_value in catalog.get(option_id, ()):
            return {option_id: selected_value}
        if option_id in catalog:
            logger.warning(
                "ACP session '%s' does not offer '%s' for '%s'.",
                request.session_id,
                selected_value,
                option_id,
            )
    return {}


async def main() -> None:
    load_dotenv()
    settings = Settings()

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("acp_client_agent")

    # Command to spawn the remote ACP agent
    acp_command = shlex.split(settings.acp_agent_command)

    # Working directory for ACP sessions
    acp_cwd = settings.acp_agent_cwd

    # Create adapter pointing to remote ACP agent
    adapter = ACPClientAdapter(
        command=acp_command,
        cwd=acp_cwd,
        resolve_session_config=partial(
            choose_session_config,
            preferences=settings.session_config_preferences,
        ),
    )

    logger.info(
        "Starting ACP client bridge (forwarding to '%s')...",
        " ".join(acp_command),
    )
    logger.info("Messages from Band will be forwarded to the ACP agent.")
    async with Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
