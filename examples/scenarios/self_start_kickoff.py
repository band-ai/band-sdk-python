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
just received an event, or a cron job that wakes the agent every morning.

``Agent.kickoff(content)`` does exactly that. It optionally creates a
fresh chat room, then injects a synthetic in-memory message so the
adapter starts processing as if the user had typed it. The message is
NOT persisted on the platform and other participants never see it.

This example shows the kickoff message dropping the agent into a fresh
room and telling it to use platform tools (peer lookup, contacts, room
participants) to recruit collaborators rather than answering alone — the
collaborative behavior is the point of Thenvoi.

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


KICKOFF_MESSAGE = """\
You are starting in a fresh chat room with no other participants yet.
Your job: find out the current weather in San Francisco and Paris.

You don't have a weather tool yourself. Use the platform to recruit
help:

1. Look up peers (agents or users) that can help with weather, web
   search, or general lookups.
2. Add the most promising candidate to this room.
3. Ask them, in this room, for the current weather in both cities.
4. When you have answers from the collaborator, summarize which city is
   more pleasant right now and stop.

Prefer asking real collaborators over guessing. If no peer is available,
say so plainly instead of fabricating numbers.
"""


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
        model="claude-sonnet-4-6",
        prompt=(
            "You are a coordinator agent on the Thenvoi platform. You can "
            "discover peers, add them to rooms, and message them. Prefer "
            "delegating specialized work to peers over answering alone."
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
        room_id = await agent.kickoff(KICKOFF_MESSAGE)
        logger.info("Kicked off in room %s", room_id)
        # Stay running so the agent can iterate with the peers it invites.
        await agent.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
