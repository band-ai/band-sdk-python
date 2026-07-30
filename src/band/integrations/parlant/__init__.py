"""
Parlant SDK integration for Band SDK.

This module provides the integration with the official Parlant SDK
(https://github.com/emcie-co/parlant) for building guideline-based
conversational AI agents.

Usage — the reserved ports are what lets several agents share one host, instead of
each taking Parlant's fixed defaults and colliding:
    import parlant.sdk as p
    from band import Agent
    from band.adapters import ParlantAdapter
    from band.integrations.parlant import reserve_server_ports

    ports = reserve_server_ports()

    async with p.Server(
        port=ports.port,
        tool_service_port=ports.tool_service_port,
    ) as server:
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
