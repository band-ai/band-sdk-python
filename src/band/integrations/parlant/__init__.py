"""
Parlant SDK integration for Band SDK.

This module provides the integration with the official Parlant SDK
(https://github.com/emcie-co/parlant) for building guideline-based
conversational AI agents.

Usage (to run several agents on one host, pass ``reserve_server_ports()`` to
``p.Server`` instead of letting it take its fixed default ports):
    import parlant.sdk as p
    from band import Agent
    from band.adapters import ParlantAdapter

    async with p.Server() as server:
        agent = await server.create_agent(
            name="Assistant",
            description="A helpful assistant",
        )

        adapter = ParlantAdapter(
            server=server,
            parlant_agent=agent,
        )

        band_agent = Agent.create(
            adapter=adapter,
            agent_id="...",
            api_key="...",
        )
        await band_agent.run()
"""

from __future__ import annotations

from band.integrations.parlant.ports import ServerPorts, reserve_server_ports

__all__ = ["ServerPorts", "reserve_server_ports"]
