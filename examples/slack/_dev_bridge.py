"""Manual smoke driver for INT-461 (wrapping shape).

One Agent process. One Thenvoi identity. The agent has a brain (here
``AnthropicAdapter``) and ``SlackAdapter`` is layered on top so that
both Slack webhooks AND Thenvoi WS messages flow into the same brain.
Slack threads are mirrored into Thenvoi rooms one-to-one.

Not part of the shipped examples — Step 11 will write the official
``examples/slack/01_basic_bot.py``.

Run with:
    export SLACK_SIGNING_SECRET=...
    export SLACK_BOT_TOKEN=xoxb-...
    export THENVOI_AGENT_ID=<the agent's uuid>
    export THENVOI_API_KEY=<the agent's api key>
    export ANTHROPIC_API_KEY=sk-ant-...
    # optional:
    export THENVOI_REST_URL=https://app.thenvoi.com
    export THENVOI_WS_URL=wss://app.thenvoi.com/api/v1/socket/websocket
    export SLACK_BOT_MODEL=claude-sonnet-4-6
    uv run python examples/slack/_dev_bridge.py
"""

from __future__ import annotations

import asyncio
import logging
import os

import uvicorn
from starlette.applications import Starlette

from thenvoi import Agent
from thenvoi.adapters import AnthropicAdapter
from thenvoi.integrations.slack import SlackAdapter, SlackApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


async def main() -> None:
    signing_secret = os.environ["SLACK_SIGNING_SECRET"]
    bot_token = os.environ["SLACK_BOT_TOKEN"]
    agent_id = os.environ["THENVOI_AGENT_ID"]
    api_key = os.environ["THENVOI_API_KEY"]
    # AnthropicAdapter reads ANTHROPIC_API_KEY from the env on its own.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise ValueError("ANTHROPIC_API_KEY is required")

    rest_url = os.environ.get("THENVOI_REST_URL", "https://app.thenvoi.com")
    ws_url = os.environ.get(
        "THENVOI_WS_URL", "wss://app.thenvoi.com/api/v1/socket/websocket"
    )
    model = os.environ.get("SLACK_BOT_MODEL", "claude-sonnet-4-6")

    # Slack plan-block visibility is now independent of the brain's
    # ``Emit.EXECUTION`` setting — ``SlackAdapter`` observes tool
    # execution directly via its tools wrapper. To ALSO record
    # ``tool_call``/``tool_result`` events on the Thenvoi side, pass
    # ``features=AdapterFeatures(emit={Emit.EXECUTION})`` here.
    brain = AnthropicAdapter(model=model)
    slack = SlackAdapter(
        inner=brain,
        apps=[
            SlackApp(
                slug="dev",
                signing_secret=signing_secret,
                bot_token=bot_token,
            ),
        ],
        rest_url=rest_url,
        api_key=api_key,
    )
    agent = Agent.create(
        adapter=slack,
        agent_id=agent_id,
        api_key=api_key,
        rest_url=rest_url,
        ws_url=ws_url,
    )

    starlette_app = Starlette()
    starlette_app.mount("/slack", slack.router)
    config = uvicorn.Config(starlette_app, host="0.0.0.0", port=3000, log_level="info")
    server = uvicorn.Server(config)

    async with agent:
        await asyncio.gather(
            agent.run_forever(),
            server.serve(),
        )


if __name__ == "__main__":
    asyncio.run(main())
