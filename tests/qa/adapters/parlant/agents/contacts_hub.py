"""Parlant expanded agent for Scenario F3 (contact strategy HUB_ROOM).

Contact requests are routed into a dedicated hub room where the Parlant agent's
LLM decides whether to approve or reject them.
"""

from __future__ import annotations

import asyncio

from _common import run_parlant_agent

from thenvoi.core.types import AdapterFeatures, Capability, Emit
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

HUB_DESCRIPTION = """You are a contact-management assistant on the Thenvoi platform.

When the user asks you to manage contacts — list, add, remove, list requests, or
respond to requests — use the appropriate contact tools. When a contact request
arrives in your hub room, read the requester's message and decide whether to
approve or reject it. Approve genuine, friendly collaboration requests; reject
spam, scams, or suspicious requests.
"""


if __name__ == "__main__":
    asyncio.run(
        run_parlant_agent(
            "parlant_contacts_test",
            features=AdapterFeatures(
                capabilities={Capability.CONTACTS}, emit={Emit.EXECUTION}
            ),
            description=HUB_DESCRIPTION,
            contact_config=ContactEventConfig(
                strategy=ContactEventStrategy.HUB_ROOM,
                broadcast_changes=True,
            ),
        )
    )
