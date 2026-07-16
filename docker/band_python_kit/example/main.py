"""Minimal Band agent for the band-python-kit example workspace.

The kit's launcher execs this file with the workspace's own locked venv
interpreter; Band identity, endpoints, and credentials arrive as environment
variables. This example echoes every message — swap ``EchoAdapter`` for any
framework adapter (``band.adapters.*``) and add the matching ``band-sdk``
extra to pyproject.toml to run a real LLM agent.
"""

from __future__ import annotations

import asyncio
import os

from band import Agent
from band.core.simple_adapter import SimpleAdapter
from band.runtime.shutdown import run_with_graceful_shutdown


class EchoAdapter(SimpleAdapter[str]):
    async def on_message(
        self,
        msg,
        tools,
        history,
        participants_msg,
        contacts_msg,
        *,
        is_session_bootstrap,
        room_id,
    ):
        await tools.send_message(f"echo: {msg.content}", mentions=[msg.sender_id])


async def main() -> None:
    agent = Agent.create(
        adapter=EchoAdapter(),
        agent_id=os.environ["BAND_AGENT_ID"],
        api_key=os.environ["BAND_API_KEY"],
        ws_url=os.environ["BAND_WS_URL"],
        rest_url=os.environ["BAND_REST_URL"],
    )
    # Signals reach this process directly (the launcher execs into it), so
    # SIGTERM from `sbx stop` shuts the agent down gracefully.
    await run_with_graceful_shutdown(agent)


if __name__ == "__main__":
    asyncio.run(main())
