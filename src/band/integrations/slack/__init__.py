"""Slack integration for Band SDK.

Wraps an inner framework adapter (the brain) so one Band agent can
receive Slack Events API traffic and reply back into the originating
Slack thread. This is a wrapper around an inner adapter, not a
peer-slug gateway.

Example:
    from band import Agent
    from band.adapters import AnthropicAdapter, AnthropicAdapterConfig
    from band.integrations.slack import SlackAdapter, SlackAdapterConfig, SlackApp

    brain = AnthropicAdapter(AnthropicAdapterConfig(model="claude-sonnet-4-6"))

    slack = SlackAdapter(
        SlackAdapterConfig(
            apps=(
                SlackApp(
                    slug="recruit",
                    signing_secret="...",
                    bot_token="xoxb-...",
                ),
            ),
        ),
        inner=brain,
    )

    agent = Agent.create(adapter=slack, agent_id="slack-bridge", api_key="...")

    # HTTP transport: mount slack.router into your ASGI app, e.g.:
    #     app.mount("/slack", slack.router)
    await agent.run()
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from band.exports import lazy_exports

if TYPE_CHECKING:
    from band.integrations.slack.adapter import SlackAdapter as SlackAdapter
    from band.integrations.slack.adapter import (
        SlackAdapterConfig as SlackAdapterConfig,
    )
    from band.integrations.slack.types import (
        SlackApp as SlackApp,
    )
    from band.integrations.slack.types import (
        SlackSessionState as SlackSessionState,
    )

__all__, __getattr__ = lazy_exports(
    __name__,
    adapter=["SlackAdapter", "SlackAdapterConfig"],
    types=["SlackApp", "SlackSessionState"],
)
