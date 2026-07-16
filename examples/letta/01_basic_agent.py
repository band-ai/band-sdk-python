# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[letta]", "pydantic-settings", "python-dotenv"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Letta agent example.

Connects a Letta agent to the Band platform using MCP tools for
bidirectional communication.  Works with both Letta Cloud and self-hosted
Letta servers.

Environment variables:
    BAND_WS_URL      Band WebSocket URL (required)
    BAND_REST_URL    Band REST URL (required)
    LETTA_BASE_URL      Letta server URL (default: https://api.letta.com)
                        Set to http://localhost:8283 for self-hosted.
    LETTA_API_KEY       Letta API key (required for Cloud, optional for self-hosted)
    LETTA_PROJECT       Letta Cloud project name (optional)
    LETTA_MODEL         LLM model ID (default: openai/gpt-5.4-mini)
                        Must include the provider prefix, e.g.
                        openai/gpt-5.4-mini or
                        anthropic/claude-haiku-4-5
    LETTA_EMBEDDING     Embedding model for agent create (required by Letta's
                        Docker server, e.g. openai/text-embedding-3-small)
    LETTA_MCP_ADVERTISED_HOST
                        Host the Letta server uses to reach the adapter's
                        self-hosted MCP server (default: host.docker.internal
                        for Docker; set 127.0.0.1 for native Letta)
    MCP_SERVER_URL      Optional external band-mcp server URL. When unset, the
                        adapter self-hosts its own Band MCP server in-process.
                        Must be reachable by the Letta server. For Letta Cloud
                        this must be a publicly reachable URL (e.g. via ngrok).

Letta Cloud usage:
    export LETTA_API_KEY="your-letta-cloud-api-key"
    # Letta Cloud cannot reach a laptop-local server, so point it at a
    # publicly reachable band-mcp
    export MCP_SERVER_URL="https://your-mcp-server.example.com/sse"
    uv run examples/letta/01_basic_agent.py

Self-hosted usage:
    export LETTA_BASE_URL="http://localhost:8283"
    export LETTA_MODEL="anthropic/claude-haiku-4-5"
    export LETTA_EMBEDDING="openai/text-embedding-3-small"
    # No LETTA_API_KEY and no MCP_SERVER_URL needed: the adapter self-hosts
    # the MCP server and the dockerized Letta reaches it through the host.
    docker run --add-host=host.docker.internal:host-gateway \
        -p 8283:8283 letta/letta:latest
    uv run examples/letta/01_basic_agent.py

Troubleshooting:
    If Letta returns "INVALID_ARGUMENT: The model handle should be in the
    format provider/model-name", set LETTA_MODEL to a full Letta model handle
    such as "openai/gpt-5.4-mini" or
    "anthropic/claude-haiku-4-5". A bare model name from another
    variable, for example "claude-haiku-4-5-20251001", is not accepted.
    If Letta returns "Handle ... not found", check the handles exposed by your
    local server:

        curl -fsS http://localhost:8283/v1/models/
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from setup_logging import setup_logging

from band import Agent
from band.adapters.letta import LettaAdapter, LettaAdapterConfig, LettaMCPConfig

setup_logging()
logger = logging.getLogger(__name__)


class ExampleSettings(BaseSettings):
    """Example environment (field name == env var, see module docstring)."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    band_ws_url: str = ""  # BAND_WS_URL (required)
    band_rest_url: str = ""  # BAND_REST_URL (required)
    letta_base_url: str = "https://api.letta.com"  # LETTA_BASE_URL
    letta_api_key: str | None = None  # LETTA_API_KEY (required for Letta Cloud)
    letta_project: str | None = None  # LETTA_PROJECT
    letta_model: str = "openai/gpt-5.4-mini"  # LETTA_MODEL
    letta_embedding: str | None = None  # LETTA_EMBEDDING
    letta_mcp_advertised_host: str = "host.docker.internal"  # LETTA_MCP_ADVERTISED_HOST
    mcp_server_url: str | None = None  # MCP_SERVER_URL (external band-mcp)


async def main() -> None:
    load_dotenv()
    settings = ExampleSettings()

    if not settings.band_ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not settings.band_rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
    # An explicit MCP_SERVER_URL selects an external band-mcp (the Letta Cloud
    # topology); otherwise the adapter self-hosts its Band MCP server in-process.
    if settings.mcp_server_url:
        mcp_config = LettaMCPConfig(mode="external", server_url=settings.mcp_server_url)
    else:
        advertised_host = settings.letta_mcp_advertised_host
        loopback = advertised_host in ("127.0.0.1", "localhost", "::1")
        mcp_config = LettaMCPConfig(
            bind_host="127.0.0.1" if loopback else "0.0.0.0",
            advertised_host=advertised_host,
        )

    # Create adapter — defaults to Letta Cloud (https://api.letta.com).
    # For self-hosted, set LETTA_BASE_URL=http://localhost:8283
    adapter = LettaAdapter(
        config=LettaAdapterConfig(
            base_url=settings.letta_base_url,
            # Required for Letta Cloud, optional for self-hosted
            provider_key=settings.letta_api_key,
            # Letta Cloud project scoping (optional)
            project=settings.letta_project,
            model=settings.letta_model,
            # Required by Letta's Docker server on agent create
            embedding=settings.letta_embedding,
            # MCP tool path (self-hosted unless MCP_SERVER_URL is set)
            mcp=mcp_config,
            custom_section="You are a helpful assistant. Be concise and friendly.",
        ),
    )

    # Create and start agent
    agent = Agent.from_config(
        "letta_agent",
        adapter=adapter,
        ws_url=settings.band_ws_url,
        rest_url=settings.band_rest_url,
    )

    logger.info("Starting Letta agent...")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
