"""A2A protocol integration for Band SDK.

This module provides integration with A2A (Agent-to-Agent) protocol, allowing
remote A2A-compliant agents to participate in Band chat rooms as peers.

Example:
    from band import Agent
    from band.integrations.a2a import A2AAdapter, A2AAuth

    # Basic usage
    adapter = A2AAdapter(
        remote_url="https://currency-agent.example.com",
    )

    # With authentication
    adapter = A2AAdapter(
        remote_url="https://currency-agent.example.com",
        auth=A2AAuth(api_key="my-secret-key"),
    )

    # Create agent and run
    agent = Agent.create(
        adapter=adapter,
        agent_id="currency-bot",
        api_key="your-band-api-key",
    )
    await agent.run()
"""

from band.integrations.a2a.adapter import A2AAdapter
from band.integrations.a2a.types import A2AAuth, A2ASessionState

__all__ = ["A2AAdapter", "A2AAuth", "A2ASessionState"]
