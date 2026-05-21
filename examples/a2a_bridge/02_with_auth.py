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
import json
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import Agent
from band.adapters import A2AAdapter
from band.config import load_agent_config
from band.integrations.a2a import A2AAuth

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")

    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")

    # Load agent credentials from agent_config.yaml
    agent_id, api_key = load_agent_config("a2a_agent")

    # URL of the remote A2A agent
    a2a_url = os.getenv("A2A_AGENT_URL", "http://localhost:10000")

    # A2A agent authentication (if required)
    a2a_api_key = os.getenv("A2A_API_KEY")
    a2a_bearer_token = os.getenv("A2A_BEARER_TOKEN")
    a2a_auth_headers_json = os.getenv("A2A_AUTH_HEADERS_JSON")

    custom_headers: dict[str, str] = {}
    if a2a_auth_headers_json:
        try:
            parsed_headers = json.loads(a2a_auth_headers_json)
        except json.JSONDecodeError as exc:
            raise ValueError("A2A_AUTH_HEADERS_JSON must be valid JSON") from exc
        if not isinstance(parsed_headers, dict):
            raise ValueError("A2A_AUTH_HEADERS_JSON must decode to a JSON object")
        invalid_header_values = [
            key for key, value in parsed_headers.items() if not isinstance(value, str)
        ]
        if invalid_header_values:
            raise ValueError(
                "A2A_AUTH_HEADERS_JSON values must all be strings; "
                f"invalid keys: {', '.join(sorted(invalid_header_values))}"
            )
        custom_headers = {
            str(key): value
            for key, value in parsed_headers.items()
            if isinstance(key, str)
        }

    # Configure auth if credentials provided
    auth = None
    if a2a_api_key or a2a_bearer_token or custom_headers:
        auth = A2AAuth(
            api_key=a2a_api_key,
            bearer_token=a2a_bearer_token,
            headers=custom_headers,
        )
        logger.info(
            "Using authentication for A2A agent (api_key=%s, bearer=%s, custom_headers=%s)",
            bool(a2a_api_key),
            bool(a2a_bearer_token),
            sorted(custom_headers),
        )

    # Create adapter with auth
    adapter = A2AAdapter(
        remote_url=a2a_url,
        auth=auth,
        streaming=True,
    )

    # Create and start agent
    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting A2A bridge agent (forwarding to %s)...", a2a_url)
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
