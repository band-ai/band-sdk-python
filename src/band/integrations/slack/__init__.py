"""Slack integration for Band SDK.

Wraps an inner framework adapter (the brain) so one Band agent can
receive Slack Events API traffic and reply back into the originating
Slack thread. This is a wrapper around an inner adapter, not a
peer-slug gateway.

Example:
    from band import Agent, SlackGateway
    from band.adapters import AnthropicAdapter
    from band.integrations.slack import SlackAdapter, SlackApp

    brain = AnthropicAdapter(model="claude-sonnet-4-6")

    slack = SlackAdapter(
        inner=brain,
        apps=[
            SlackApp(
                slug="recruit",
                signing_secret="...",
                bot_token="xoxb-...",
            ),
        ],
        rest_url="https://app.band.ai",
        api_key="...",
    )

    agent = Agent.create(adapter=slack, agent_id="slack-bridge", api_key="...")

    async with SlackGateway(agent=agent) as gateway:
        await gateway.serve()
"""

from band.integrations.slack.adapter import SlackAdapter
from band.integrations.slack.host import SlackGateway
from band.integrations.slack.types import SlackApp, SlackSessionState, SlackTransport

__all__ = [
    "SlackAdapter",
    "SlackApp",
    "SlackGateway",
    "SlackSessionState",
    "SlackTransport",
]
