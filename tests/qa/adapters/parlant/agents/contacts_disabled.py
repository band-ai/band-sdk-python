"""Parlant expanded agent for Scenario F1 (contact strategy DISABLED)."""

from __future__ import annotations

import asyncio

from _common import run_parlant_agent

from thenvoi.core.types import AdapterFeatures, Capability, Emit
from thenvoi.runtime.types import ContactEventConfig, ContactEventStrategy

CONTACTS_DESCRIPTION = """You are a contact-management assistant on the Thenvoi platform.

When the user asks you to manage contacts — list, add, remove, list requests, or
respond to requests — use the appropriate contact tools.
"""


if __name__ == "__main__":
    asyncio.run(
        run_parlant_agent(
            "parlant_contacts_test",
            features=AdapterFeatures(
                capabilities={Capability.CONTACTS}, emit={Emit.EXECUTION}
            ),
            description=CONTACTS_DESCRIPTION,
            contact_config=ContactEventConfig(strategy=ContactEventStrategy.DISABLED),
        )
    )
