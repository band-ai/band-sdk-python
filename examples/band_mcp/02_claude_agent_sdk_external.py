#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-mcp", "claude-agent-sdk>=0.1.81"]
# ///
"""
Vanilla Claude Agent SDK script — durable memory across independent sessions.

No `band-sdk`, no `ClaudeSDKAdapter`, no `Agent.create`: this is what a
Claude Agent SDK user reaches for on their own, wiring Band in exactly like
Claude Desktop or Cursor would via `mcp_config_example.json`. Contrast with
`ClaudeSDKAdapter`, which hands Claude an in-process `LocalMCPServer`
(`mcp_servers={"band": <server object>}`); here Claude spawns `band-mcp` as
its own subprocess (`{"type": "stdio", "command": "band-mcp", ...}`) and the
two processes never share Python state.

The point of this example specifically: `--tools memory` gives *any*
external agent script durable, cross-session memory with no shared Python
state at all. Two fully independent `ClaudeSDKClient` sessions run below —
each spawns its own fresh `band-mcp` subprocess — to prove the second
session can recall what the first one stored, purely through Band as the
persistence layer.

Prerequisites:
    1. Node.js 20+ and the Claude Code CLI: npm install -g @anthropic-ai/claude-code
    2. An agent-scoped Band API key (BAND_AGENT_KEY, starts with `band_a_`)
    3. ANTHROPIC_API_KEY

Run with:
    BAND_AGENT_KEY=band_a_... ANTHROPIC_API_KEY=... \
        uv run examples/band_mcp/02_claude_agent_sdk_external.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    TextBlock,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _options(agent_key: str, base_url: str) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        system_prompt="You are a Band agent with access to durable memory tools.",
        mcp_servers={
            "band": {
                "type": "stdio",
                "command": "band-mcp",
                "args": ["--scope", "agent", "--tools", "memory"],
                "env": {"BAND_AGENT_KEY": agent_key, "BAND_BASE_URL": base_url},
            }
        },
        # No human is present to approve tool calls in this headless script;
        # every band-mcp call is a Band-scoped API call, not a filesystem/shell
        # action, so bypassing the approval prompt is safe here.
        permission_mode="bypassPermissions",
        setting_sources=[],
    )


async def run_turn(options: ClaudeAgentOptions, prompt: str) -> str:
    """Run one query against a fresh ClaudeSDKClient and return the reply text."""
    reply_parts: list[str] = []
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        reply_parts.append(block.text)
    return "\n".join(reply_parts)


async def main() -> None:
    agent_key = os.environ["BAND_AGENT_KEY"]
    base_url = os.environ.get("BAND_BASE_URL", "https://app.band.ai")
    options = _options(agent_key, base_url)

    # A nonce makes the fact unambiguously new, so the recall in session 2
    # can only succeed by actually reading it back from Band, not by the
    # model already "knowing" it.
    nonce = uuid.uuid4().hex[:8]
    fact = f"The secret band-mcp demo passphrase is 'stdio-{nonce}'."

    logger.info("--- Session 1: storing a memory (fresh band-mcp subprocess) ---")
    store_reply = await run_turn(
        options,
        "Store this fact as a durable memory using band_store_memory with "
        f'system="long_term", type="semantic", segment="agent", scope="agent": '
        f'"{fact}" Then tell me the memory id you got back.',
    )
    logger.info("Claude (session 1): %s", store_reply)

    logger.info(
        "--- Session 2: recalling it (a brand new subprocess, no shared state) ---"
    )
    recall_reply = await run_turn(
        options,
        "Use band_list_memories to search your agent-scoped semantic memories "
        f'for content containing "stdio-{nonce}", then tell me the passphrase you found.',
    )
    logger.info("Claude (session 2): %s", recall_reply)


if __name__ == "__main__":
    asyncio.run(main())
