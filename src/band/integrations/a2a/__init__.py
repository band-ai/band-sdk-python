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

from __future__ import annotations

from typing import TYPE_CHECKING

from band.exports import lazy_exports

# Type-only imports for static analysis (pyrefly, mypy, etc.)
if TYPE_CHECKING:
    from band.integrations.a2a.adapter import A2AAdapter as A2AAdapter
    from band.integrations.a2a.types import (
        A2AAuth as A2AAuth,
        A2ASessionState as A2ASessionState,
    )

__all__, __getattr__ = lazy_exports(
    __name__,
    adapter=["A2AAdapter"],
    types=["A2AAuth", "A2ASessionState"],
)
