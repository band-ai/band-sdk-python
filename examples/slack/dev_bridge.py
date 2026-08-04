"""Manual smoke driver for the Slack bridge (wrapping shape).

One Agent process. One Band identity. The agent has a brain (here
``AnthropicAdapter``) and ``SlackAdapter`` is layered on top so that
both Slack webhooks AND Band WS messages flow into the same brain.
Slack threads are mirrored into Band rooms one-to-one.

Not part of the shipped examples — see ``examples/slack/01_basic_bot.py``
for the official getting-started example.

Run with:
    export SLACK_BOT_TOKEN=xoxb-...
    export BAND_AGENT_ID=<the agent's uuid>
    export BAND_API_KEY=<the agent's api key>
    export ANTHROPIC_API_KEY=sk-ant-...

    # HTTP transport (default) — needs a public URL pointing at port 3000:
    export SLACK_SIGNING_SECRET=...
    uv run python examples/slack/dev_bridge.py

    # Socket Mode — no public URL or signing secret needed:
    export SLACK_TRANSPORT=socket
    export SLACK_APP_TOKEN=xapp-...
    uv run python examples/slack/dev_bridge.py

    # optional:
    export BAND_REST_URL=https://app.band.ai
    export BAND_WS_URL=wss://app.band.ai/api/v1/socket/websocket
    export SLACK_BOT_MODEL=claude-sonnet-4-6
"""

from __future__ import annotations

import asyncio
import logging

from pydantic_settings import BaseSettings, SettingsConfigDict

from band import Agent
from band.adapters import AnthropicAdapter
from band.integrations.slack import SlackAdapter, SlackApp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    slack_transport: str = "http"
    slack_bot_token: str
    band_agent_id: str
    band_api_key: str
    # AnthropicAdapter reads ANTHROPIC_API_KEY from the env on its own; required
    # here too so a missing key fails before any Slack/Band setup runs.
    anthropic_api_key: str
    slack_signing_secret: str = ""
    slack_app_token: str = ""
    slack_bot_model: str = "claude-sonnet-4-6"


async def main() -> None:
    settings = Settings()

    transport = settings.slack_transport.lower()
    if transport not in ("http", "socket"):
        raise ValueError(
            f"SLACK_TRANSPORT must be 'http' or 'socket', got {transport!r}"
        )

    if transport == "http":
        if not settings.slack_signing_secret:
            raise ValueError("SLACK_SIGNING_SECRET environment variable is required")
        signing_secret = settings.slack_signing_secret
        app_token = ""
    else:
        if not settings.slack_app_token:
            raise ValueError("SLACK_APP_TOKEN environment variable is required")
        signing_secret = ""
        app_token = settings.slack_app_token

    model = settings.slack_bot_model

    # Slack plan-block visibility is independent of the brain's ``emit``
    # setting — ``SlackAdapter`` observes tool execution directly via its
    # tools wrapper. The brain's default ``emit`` already records
    # ``tool_call``/``tool_result`` events on the Band side too; narrow it
    # with ``emit=`` if that's not wanted.
    brain = AnthropicAdapter(model=model)
    slack = SlackAdapter(
        inner=brain,
        apps=[
            SlackApp(
                slug="dev",
                bot_token=settings.slack_bot_token,
                signing_secret=signing_secret,
                app_token=app_token,
            ),
        ],
        transport=transport,  # type: ignore[arg-type]
    )
    agent = Agent.create(
        adapter=slack,
        agent_id=settings.band_agent_id,
        api_key=settings.band_api_key,
    )

    if transport == "http":
        # Mount the Slack router into a tiny ASGI app and run uvicorn
        # alongside the Band WS agent loop.
        import uvicorn
        from starlette.applications import Starlette

        starlette_app = Starlette()
        starlette_app.mount("/slack", slack.router)
        config = uvicorn.Config(
            starlette_app, host="0.0.0.0", port=3000, log_level="info"
        )
        server = uvicorn.Server(config)
        async with agent:
            try:
                await asyncio.gather(
                    agent.run_forever(),
                    server.serve(),
                )
            finally:
                await slack.close()
    else:
        # Socket Mode: no HTTP surface. ``slack.on_started`` (invoked by
        # Agent.__aenter__) opens the per-app websocket; we just keep
        # the Band WS agent loop running until cancelled.
        async with agent:
            try:
                await agent.run_forever()
            finally:
                await slack.close()


if __name__ == "__main__":
    asyncio.run(main())
