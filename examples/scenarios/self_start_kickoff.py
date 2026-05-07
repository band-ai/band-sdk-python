# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[anthropic]", "python-dotenv"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Self-starting agent example using ``Agent.kickoff()``.

Most agents react to messages from the platform. Sometimes you want the
opposite: the agent should start working on its own, with an initial
message that did NOT come from a real user — for example, a webhook that
just received a deploy event, or a cron job that wakes an agent every
morning to file a daily report.

``Agent.kickoff(content)`` does exactly that. It optionally creates a
fresh chat room, then injects a synthetic in-memory message so the
adapter starts processing as if the user had typed it. The message is
NOT persisted on the platform and other participants never see it.

Run with:
    uv run examples/scenarios/self_start_kickoff.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from anthropic.setup_logging import setup_logging  # noqa: E402
from thenvoi import Agent  # noqa: E402
from thenvoi.adapters import AnthropicAdapter  # noqa: E402
from thenvoi.config import load_agent_config  # noqa: E402

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

    agent_id, api_key = load_agent_config("anthropic_agent")

    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=(
            "You are a helpful assistant. When kicked off, do the work the "
            "kickoff message asks for and report your findings concisely."
        ),
    )

    agent = Agent.create(
        adapter=adapter,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    async with agent:
        # Create a fresh chat room and seed it with the initial task. The
        # adapter will receive this as a normal message and respond as it
        # would to any other input.
        room_id = await agent.kickoff(
            "Please look up the weather in San Francisco and Paris, then "
            "summarize which one is more pleasant right now."
        )
        logger.info("Kicked off in room %s", room_id)

        # Keep running so the agent can finish the work, observe its own
        # response, and respond to any human follow-ups in the new room.
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
