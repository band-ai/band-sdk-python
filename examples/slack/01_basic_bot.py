# /// script
# requires-python = ">=3.11"
# dependencies = ["thenvoi-sdk[slack,anthropic]"]
#
# [tool.uv.sources]
# thenvoi-sdk = { git = "https://github.com/thenvoi/thenvoi-sdk-python.git" }
# ///
"""
Basic Slack bot: wrap an Anthropic brain with the SlackAdapter and
expose it as a Slack-native AI app. Defaults to Socket Mode so you
don't need a public URL or ngrok to get started.

Setup
-----
1. Register your Slack app from the bundled manifest:
   ``src/thenvoi/integrations/slack/templates/manifest.yaml``.
   That manifest declares every scope and event subscription this
   example expects. (The recommended "Delayed Events" toggle has no
   manifest field — enable it manually under Event Subscriptions; see
   step 7 in the manifest header.)

2. Install the app to your workspace, then grab the Bot Token
   (``xoxb-...``). For Socket Mode also generate an App-Level Token
   (``xapp-...``) with the ``connections:write`` scope under
   "Basic Information" → "App-Level Tokens".

3. ``/invite @your-bot`` in any channel you want the bot to read.
   Without channel membership ``conversations.replies`` returns
   ``not_in_channel`` and the brain loses thread context.

4. Add the agent credentials to ``agent_config.yaml`` under the key
   ``slack_basic_bot``::

       slack_basic_bot:
         agent_id: "..."
         api_key: "..."

5. Set the Slack + Anthropic env vars (e.g. via ``.env``)::

       SLACK_BOT_TOKEN=xoxb-...
       SLACK_APP_TOKEN=xapp-...     # Socket Mode only
       SLACK_SIGNING_SECRET=...     # HTTP transport only
       ANTHROPIC_API_KEY=sk-ant-...
       THENVOI_REST_URL=...
       THENVOI_WS_URL=...

Run with
--------
    uv run examples/slack/01_basic_bot.py

HTTP transport
--------------
Set ``SLACK_TRANSPORT=http`` and provide ``SLACK_SIGNING_SECRET``.
The example below mounts ``slack.router`` into a Starlette app on
port 3000; point Slack's Event Subscriptions request URL at
``https://<your-public-host>/slack/dev/events``. To embed in an
existing FastAPI service instead::

    from fastapi import FastAPI
    app = FastAPI()
    app.mount("/slack", slack.router)
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from thenvoi import AdapterFeatures, Agent, Emit
from thenvoi.adapters import AnthropicAdapter
from thenvoi.config import load_agent_config
from thenvoi.integrations.slack import SlackAdapter, SlackApp

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    transport = os.getenv("SLACK_TRANSPORT", "socket").lower()
    if transport not in ("http", "socket"):
        raise ValueError(
            f"SLACK_TRANSPORT must be 'http' or 'socket', got {transport!r}"
        )

    ws_url = os.getenv("THENVOI_WS_URL")
    rest_url = os.getenv("THENVOI_REST_URL")
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not ws_url:
        raise ValueError("THENVOI_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("THENVOI_REST_URL environment variable is required")
    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN environment variable is required")

    if transport == "socket":
        app_token = os.getenv("SLACK_APP_TOKEN")
        if not app_token:
            raise ValueError(
                "SLACK_APP_TOKEN (xapp-...) is required when SLACK_TRANSPORT=socket"
            )
        signing_secret = ""
    else:
        signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
        if not signing_secret:
            raise ValueError(
                "SLACK_SIGNING_SECRET is required when SLACK_TRANSPORT=http"
            )
        app_token = ""

    agent_id, api_key = load_agent_config("slack_basic_bot")

    # AnthropicAdapter reads ANTHROPIC_API_KEY from the environment.
    #
    # features=AdapterFeatures(emit={Emit.EXECUTION}) enables tool-call
    # emission: every tool the brain runs is recorded into the Thenvoi room
    # as tool_call / tool_result events, so the room's audit timeline shows
    # what the agent did and with what result. This is the Thenvoi-side
    # record; the Slack-side plan/task progress blocks are a separate knob
    # (SlackAdapter(show_tool_progress=...), on by default).
    brain = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=(
            "You are a helpful Slack assistant. Keep replies concise and "
            "use Slack-flavored markdown when it improves readability."
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )

    slack = SlackAdapter(
        inner=brain,
        apps=[
            SlackApp(
                slug="dev",
                bot_token=bot_token,
                signing_secret=signing_secret,
                app_token=app_token,
            ),
        ],
        rest_url=rest_url,
        api_key=api_key,
        transport=transport,  # type: ignore[arg-type]
    )

    agent = Agent.create(
        adapter=slack,
        agent_id=agent_id,
        api_key=api_key,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("Starting Slack bot (transport=%s)...", transport)

    if transport == "socket":
        async with agent:
            try:
                await agent.run_forever()
            finally:
                await slack.close()
    else:
        # HTTP transport: serve the Slack router alongside the agent.
        # In a real service you'd mount ``slack.router`` into your
        # existing FastAPI/Starlette app instead of running uvicorn
        # standalone like this.
        import uvicorn
        from starlette.applications import Starlette

        web_app = Starlette()
        web_app.mount("/slack", slack.router)
        config = uvicorn.Config(web_app, host="0.0.0.0", port=3000, log_level="info")
        server = uvicorn.Server(config)
        async with agent:
            try:
                await asyncio.gather(agent.run_forever(), server.serve())
            finally:
                await slack.close()


if __name__ == "__main__":
    asyncio.run(main())
