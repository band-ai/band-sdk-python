# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[a2a]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
A2A adapter with authentication example.

This example shows how to connect to a remote A2A agent that requires
authentication (API key, bearer token, or custom headers).

Optional auth environment variables:
    - A2A_API_KEY
    - A2A_BEARER_TOKEN
    - A2A_AUTH_HEADERS_JSON='{"X-Custom-Auth":"value"}'

Run with:
    uv run examples/a2a_bridge/02_with_auth.py
"""

from __future__ import annotations

import asyncio
import logging

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent, configure_logging
from band.adapters import A2AAdapter
from band.integrations.a2a import A2AAuth

configure_logging(logging.INFO)
logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    a2a_agent_url: str = "http://localhost:10000"
    a2a_api_key: str = ""
    a2a_bearer_token: str = ""
    # A JSON object env var (e.g. '{"X-Custom-Auth":"value"}'); pydantic-settings
    # decodes and validates it against dict[str, str] on construction.
    a2a_auth_headers_json: dict[str, str] = {}


async def main() -> None:
    load_dotenv()
    settings = Settings()
    a2a_url = settings.a2a_agent_url

    # Configure auth if credentials provided
    auth = None
    if (
        settings.a2a_api_key
        or settings.a2a_bearer_token
        or settings.a2a_auth_headers_json
    ):
        auth = A2AAuth(
            api_key=settings.a2a_api_key,
            bearer_token=settings.a2a_bearer_token,
            headers=settings.a2a_auth_headers_json,
        )
        logger.info(
            "Using authentication for A2A agent (api_key=%s, bearer=%s, custom_headers=%s)",
            bool(settings.a2a_api_key),
            bool(settings.a2a_bearer_token),
            sorted(settings.a2a_auth_headers_json),
        )

    # Create adapter with auth
    adapter = A2AAdapter(
        remote_url=a2a_url,
        auth=auth,
        streaming=True,
    )

    logger.info("Starting A2A bridge agent (forwarding to %s)...", a2a_url)
    async with Agent.from_config(
        "a2a_agent",
        adapter=adapter,
    ) as agent:
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
