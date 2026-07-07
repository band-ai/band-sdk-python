#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[copilot_sdk]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Human-in-the-loop via Copilot's ``ask_user`` tool, answered in the room.

``CopilotSDKAdapterConfig(ask_user="room")`` routes the model's built-in
``ask_user`` tool to the people in the Band room: the question posts as
a room message mentioning whoever triggered the turn, the turn ends, and
the answer arrives as the next room message — the same persisted Copilot
session picks it up with the pending question still in its history.

The turn *must* end when the question posts: Band delivers a room's
messages one at a time, so a turn that blocked waiting for the reply
could never receive it. The room therefore stays responsive while a
question is open — follow-up messages are processed normally.

Try it in Band chat (agent must be mentioned to be triggered):

    You:    @copilot-agent please deploy release v2.
    Agent:  Which channel should I deploy release v2 to?

            1. stable
            2. beta
            3. canary

            Reply with a number or your own answer.
    You:    @copilot-agent 2
    Agent:  Deploying v2 to the beta channel.

Prompts that reliably trigger a question:
    "@copilot-agent should we ship today or wait for more QA?"
    "@copilot-agent pick a codename for the next release."
    "@copilot-agent I need approval to archive this room's notes."

To answer from the agent's terminal instead (an operator supervising the
process rather than someone in the room), pass a handler:
``ask_user=OperatorConsole().ask`` — see the README's operator-console
section.

Prerequisites:
    1. GitHub Copilot access (token or `gh auth login` / `copilot` login)
    2. Add copilot_sdk_agent credentials to agent_config.yaml
    3. Set environment variables in .env:
       - BAND_WS_URL
       - BAND_REST_URL
       - GITHUB_TOKEN (optional — omit to use the logged-in GitHub user)

Run with:
    uv run examples/copilot_sdk/06_ask_user.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

# Add examples directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from setup_logging import setup_logging
from band import Agent
from band.adapters import CopilotSDKAdapter, CopilotSDKAdapterConfig
from band.core.types import AdapterFeatures, Emit

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the Copilot SDK agent with room-routed ask_user."""
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    adapter = CopilotSDKAdapter(
        CopilotSDKAdapterConfig(
            # ask_user="room" already teaches the model when and how to ask
            # (the adapter injects the contract into the system prompt);
            # the custom section stays for unrelated behavior.
            custom_section="Keep replies short and concrete.",
            github_token=os.getenv("GITHUB_TOKEN"),
            ask_user="room",
            # Pin a unique per-example session prefix.
            session_id_prefix="band-copilot-ask-user-",
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION, Emit.THOUGHTS}),
    )

    agent = Agent.from_config(
        "copilot_sdk_agent",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Copilot SDK ask_user agent...")
    logger.info("Agent ID: %s", agent.runtime.agent_id)
    logger.info("ask_user questions are posted into the room they came from.")
    logger.info("Press Ctrl+C to stop")

    try:
        await agent.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
