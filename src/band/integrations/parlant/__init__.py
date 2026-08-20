"""
Parlant SDK integration for Band SDK.

This module provides the integration with the official Parlant SDK
(https://github.com/emcie-co/parlant) for building guideline-based
conversational AI agents.

The ``ParlantAdapter`` owns the server lifecycle (ports, boot, teardown):

    import parlant.sdk as p
    from band import Agent
    from band.adapters import ParlantAdapter

    adapter = ParlantAdapter(
        name="Assistant",
        description="A helpful assistant",
        nlp_service=p.NLPServices.openai,
    )
    adapter.add_guideline(
        condition="User asks a question",
        action="Answer via band_send_message, mentioning the user",
    )

    band_agent = Agent.create(adapter=adapter, agent_id="...", api_key="...")
    await band_agent.run()

``running_parlant_server`` / ``reserve_server_ports`` remain available for
callers managing a Parlant server themselves (passed via ``server=``).
"""

from __future__ import annotations

from band.integrations.parlant.ports import ServerPorts, reserve_server_ports
from band.integrations.parlant.server import running_parlant_server

__all__ = ["ServerPorts", "reserve_server_ports", "running_parlant_server"]
