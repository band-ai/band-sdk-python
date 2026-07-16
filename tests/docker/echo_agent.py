"""Minimal echo agent, run inside a real band-python-kit container.

Not a pytest test module (no test_* functions) — this is real, standalone,
ruff-checked Python whose *source text* gets shipped into a container and
run via `$BAND_SDK_PYTHON -c <source>` (see tests/docker/toolkit/live_agent.py).
Keeping it a real file rather than an inline string literal means a rename
or signature change in band.core.simple_adapter/Agent.create shows up here
too, instead of silently drifting until the live test actually runs it.

Core band-sdk only (SimpleAdapter, no framework) — matches the image's
core-only default build (no SDK_EXTRA). Echoes every message, including the
first: is_session_bootstrap is True for a room's first message, but the
live test's whole scenario *is* one message in a brand-new room, so there's
no "later, real" turn to defer replying to.
"""

from __future__ import annotations

import asyncio
import os

from band import Agent
from band.core.simple_adapter import SimpleAdapter


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
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
