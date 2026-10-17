# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic] @ git+ssh://git@github.com/band-ai/band-sdk-python.git@3bac40a35f58272a7251c8f14197a367b380b9fb"]
# ///
"""Two custom tools share one HTTP client, closed by the adapter's host.

From the feature checkout: uv run --active python examples/anthropic/shared-resources.py
Configure anthropic_agent in agent_config.yaml and credentials in root .env.test.
Resources shared across different adapters should remain application-owned.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters.anthropic import AnthropicAdapter, AnthropicAdapterConfig
from band.config.loader import load_agent_config
from band.runtime.custom_tools import CustomToolDef

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
API_URL = "https://jsonplaceholder.typicode.com"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )
    SHARED_RESOURCES_AGENT: str = "anthropic_agent"
    SHARED_RESOURCES_CONFIG: Path = PROJECT_ROOT / "agent_config.yaml"
    SHARED_RESOURCES_API_URL: str = API_URL


class GetTodoInput(BaseModel):
    """Read a sample todo from JSONPlaceholder."""

    todo_id: int = Field(ge=1, le=200, description="Todo identifier")


class GetUserInput(BaseModel):
    """Read a sample user's profile from JSONPlaceholder."""

    user_id: int = Field(ge=1, le=10, description="User identifier")


def shared_tools(
    client: httpx.AsyncClient, base_url: str = API_URL
) -> list[CustomToolDef]:
    """Build both handlers around the same live client."""
    base_url = base_url.rstrip("/")

    async def get_todo(args: GetTodoInput) -> str:
        response = await client.get(f"{base_url}/todos/{args.todo_id}")
        response.raise_for_status()
        return response.text

    async def get_user(args: GetUserInput) -> str:
        response = await client.get(f"{base_url}/users/{args.user_id}")
        response.raise_for_status()
        return response.text

    return [(GetTodoInput, get_todo), (GetUserInput, get_user)]


def build_adapter(
    client: httpx.AsyncClient,
    base_url: str = API_URL,
    *,
    config: AnthropicAdapterConfig | None = None,
) -> AnthropicAdapter:
    """Register the shared client's close once, before starting its host."""
    adapter = AnthropicAdapter(config, additional_tools=shared_tools(client, base_url))
    adapter.add_cleanup(client.aclose)
    return adapter


async def build_agent(settings: Settings) -> Agent:
    """Construction failures precede handoff, so the application releases its client."""
    load_agent_config(
        settings.SHARED_RESOURCES_AGENT, config_path=settings.SHARED_RESOURCES_CONFIG
    )
    client = httpx.AsyncClient()
    try:
        return Agent.from_config(
            settings.SHARED_RESOURCES_AGENT,
            config_path=settings.SHARED_RESOURCES_CONFIG,
            adapter=build_adapter(client, settings.SHARED_RESOURCES_API_URL),
        )
    except Exception as original:
        try:
            await client.aclose()
        except Exception:
            original.add_note(
                "Application-owned client cleanup also failed before host construction"
            )
            logger.exception("Failed to close the client before host ownership")
        raise


async def serve(agent: Agent) -> None:
    """Retain the host for one cleanup retry; preserve the initiating failure."""
    try:
        async with agent:
            await agent.run_forever()
    except Exception as original:
        try:
            await agent.stop()
        except Exception:
            original.add_note(
                "Cleanup remains incomplete; retain this Agent and retry stop()"
            )
            logger.exception(
                "Cleanup remains incomplete; retain this Agent and retry stop()"
            )
        raise


async def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env.test", override=True)
    configure_logging(logging.INFO)
    agent = await build_agent(Settings())
    await serve(agent)


if __name__ == "__main__":
    asyncio.run(main())
