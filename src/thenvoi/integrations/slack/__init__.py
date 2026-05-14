"""Slack integration for Thenvoi SDK.

Exposes one or more Thenvoi peers as Slack-native AI agents. See INT-461
for the full PRD and implementation plan.

Example:
    from thenvoi import Agent
    from thenvoi.integrations.slack import SlackAdapter, SlackApp

    adapter = SlackAdapter(
        apps=[
            SlackApp(
                slug="recruit",
                signing_secret="...",
                bot_token="xoxb-...",
                target_peer_slug="recruit-assistant",
            ),
        ],
        rest_url="https://app.thenvoi.com",
        api_key="...",
    )
    agent = Agent.create(adapter=adapter, agent_id="slack-bridge", api_key="...")
    await agent.run()
"""

from thenvoi.integrations.slack.adapter import SlackAdapter
from thenvoi.integrations.slack.types import SlackApp, SlackSessionState

__all__ = ["SlackAdapter", "SlackApp", "SlackSessionState"]
