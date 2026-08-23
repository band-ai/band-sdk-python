#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-mcp", "mcp>=1.28.1,<2"]
# ///
"""
Raw MCP client talking to band-mcp over stdio — dynamic room composition.

No band-sdk, no LLM, no framework — just the `mcp` python SDK driving the
published `band-mcp` CLI as a subprocess. Demonstrates the agent-scope wire
contract for assembling a room at runtime: create it, discover another
agent via `band_lookup_peers`, pull them in with `band_add_participant`,
then `band_send_message` them directly.

Prerequisites:
    1. An agent-scoped Band API key (BAND_AGENT_KEY, starts with `band_a_`)

Run with:
    BAND_AGENT_KEY=band_a_... uv run examples/band_mcp/01_raw_client.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _server_params(
    agent_key: str, base_url: str, *, room_id: str | None
) -> StdioServerParameters:
    args = ["--scope", "agent"]
    if room_id is not None:
        # `--room-id` pins the server to one room: `chat_id` disappears from
        # the advertised schemas entirely, so calls only need the arguments
        # each tool actually cares about.
        args += ["--room-id", room_id]
    return StdioServerParameters(
        command="band-mcp",
        args=args,
        env={"BAND_AGENT_KEY": agent_key, "BAND_BASE_URL": base_url},
    )


@asynccontextmanager
async def open_session(
    agent_key: str, base_url: str, *, room_id: str | None
) -> AsyncIterator[ClientSession]:
    """Spawn band-mcp over stdio and yield an initialized ClientSession."""
    server = _server_params(agent_key, base_url, room_id=room_id)
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def create_room(agent_key: str, base_url: str) -> str:
    """Provision a fresh chat room via band_create_chatroom, unpinned.

    band_create_chatroom isn't room-bound — it takes no chat_id — so this
    runs against a plain, unpinned server before the room the rest of the
    example operates in even exists.
    """
    async with open_session(agent_key, base_url, room_id=None) as session:
        result = await session.call_tool("band_create_chatroom", {})
        return result.content[0].text


async def main() -> None:
    agent_key = os.environ["BAND_AGENT_KEY"]
    base_url = os.environ.get("BAND_BASE_URL", "https://app.band.ai")

    room_id = await create_room(agent_key, base_url)
    logger.info("Created room %s", room_id)

    async with open_session(agent_key, base_url, room_id=room_id) as session:
        tools = await session.list_tools()
        logger.info("band-mcp advertises %d tools:", len(tools.tools))
        for tool in tools.tools:
            logger.info(
                "  - %s: %s", tool.name, (tool.description or "").splitlines()[0]
            )

        # A fresh room only has its creator in it. band_lookup_peers
        # automatically excludes existing participants, so whatever it
        # returns is genuinely addable.
        peers = await session.call_tool("band_lookup_peers", {"page_size": 5})
        logger.info("Available peers: %s", peers.content)
        candidates = json.loads(peers.content[0].text)["data"]
        if not candidates:
            raise RuntimeError(
                "No peers available to add to the room. Register a second agent "
                "or add a contact on this Band account, then rerun."
            )
        # Prefer another agent over the account's own human user, to match
        # this example's "discover another agent" story.
        peer = next((c for c in candidates if c["type"] == "Agent"), candidates[0])
        peer_handle = peer["handle"]

        added = await session.call_tool(
            "band_add_participant", {"identifier": peer_handle}
        )
        logger.info("Added participant: %s", added.content)

        sent = await session.call_tool(
            "band_send_message",
            {
                "content": f"Hi @{peer_handle.split('/')[-1]}, I added you to this room "
                "over a plain MCP stdio connection — no band-sdk installed.",
                "mentions": [peer_handle],
            },
        )
        logger.info("Sent message: %s", sent.content)


if __name__ == "__main__":
    asyncio.run(main())
