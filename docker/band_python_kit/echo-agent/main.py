"""Minimal Band agent for the band-python-kit echo-agent starter workspace.

The kit's launcher execs this file with the workspace's own locked venv
interpreter; Band identity, endpoints, and credentials arrive as environment
variables. This example echoes every message — swap ``EchoAdapter`` for any
framework adapter (``band.adapters.*``) and add the matching ``band-sdk``
extra to pyproject.toml to run a real LLM agent.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.core.simple_adapter import SimpleAdapter
from band.runtime.shutdown import run_with_graceful_shutdown

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol
    from band.core.types import ChatMessage, PlatformMessage


class EchoAgentSettings(BaseSettings):
    """Band identity and endpoints, injected by the launcher as BAND_* env
    vars. A missing variable fails loud with a clear validation error."""

    model_config = SettingsConfigDict(env_prefix="BAND_", case_sensitive=False)

    agent_id: str  # BAND_AGENT_ID
    api_key: str  # BAND_API_KEY
    ws_url: str  # BAND_WS_URL
    rest_url: str  # BAND_REST_URL


class EchoAdapter(SimpleAdapter[str]):
    async def on_message(
        self,
        msg: PlatformMessage,
        tools: AgentToolsProtocol,
        history: list[ChatMessage],
        participants_msg: str | None,
        contacts_msg: str | None,
        *,
        is_session_bootstrap: bool,
        room_id: str,
    ) -> None:
        await tools.send_message(f"echo: {msg.content}", mentions=[msg.sender_id])


async def main() -> None:
    settings = EchoAgentSettings()
    agent = Agent.create(
        adapter=EchoAdapter(),
        agent_id=settings.agent_id,
        api_key=settings.api_key,
        ws_url=settings.ws_url,
        rest_url=settings.rest_url,
    )
    # Signals reach this process directly (the launcher execs into it), so
    # SIGTERM from `sbx stop` shuts the agent down gracefully.
    await run_with_graceful_shutdown(agent)


if __name__ == "__main__":
    asyncio.run(main())
