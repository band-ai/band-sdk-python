"""
Parlant SDK integration for Band SDK.

This module provides the integration with the official Parlant SDK
(https://github.com/emcie-co/parlant) for building guideline-based
conversational AI agents.

Usage:
    import parlant.sdk as p
    from thenvoi import Agent
    from thenvoi.adapters import ParlantAdapter

    async with p.Server() as server:
        agent = await server.create_agent(
            name="Assistant",
            description="A helpful assistant",
        )

        adapter = ParlantAdapter(
            server=server,
            parlant_agent=agent,
        )

        thenvoi_agent = Agent.create(
            adapter=adapter,
            agent_id="...",
            api_key="...",
        )
        await thenvoi_agent.run()
"""

__all__: list[str] = []
