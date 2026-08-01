# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[slack,anthropic]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""
Basic Slack bot: wrap an Anthropic brain with the SlackAdapter and
expose it as a Slack-native AI app. Defaults to Socket Mode so you
don't need a public URL or ngrok to get started.

Setup
-----
1. Register your Slack app from the bundled manifest:
   ``src/band/integrations/slack/templates/manifest.yaml``.
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
       BAND_REST_URL=...
       BAND_WS_URL=...

Run with
--------
    uv run examples/slack/01_basic_bot.py

HTTP transport
--------------
Set ``SLACK_TRANSPORT=http`` and provide ``SLACK_SIGNING_SECRET``.
``SlackGateway.serve()`` runs uvicorn on ``slack.port`` (default 3000)
and serves ``slack.router`` directly — point Slack's Event Subscriptions
request URL at ``https://<your-public-host>/dev/events``. To embed in
an existing FastAPI service instead, mount ``slack.router`` yourself and
keep using the legacy ``Agent.run()`` path (deprecated).
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv

from setup_logging import setup_logging
from band import AdapterFeatures, Agent, Emit, SlackGateway
from band.adapters import AnthropicAdapter
from band.config import load_agent_config
from band.integrations.slack import SlackAdapter, SlackApp

setup_logging()
logger = logging.getLogger(__name__)


async def main() -> None:
    load_dotenv()

    transport = os.getenv("SLACK_TRANSPORT", "socket").lower()
    if transport not in ("http", "socket"):
        raise ValueError(
            f"SLACK_TRANSPORT must be 'http' or 'socket', got {transport!r}"
        )

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")
    bot_token = os.getenv("SLACK_BOT_TOKEN")
    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")
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

    brain = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        instructions=(
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

    async with SlackGateway(agent=agent) as gateway:
        await gateway.serve()


if __name__ == "__main__":
    asyncio.run(main())
