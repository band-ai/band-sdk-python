"""Parlant expanded agent for Scenario F2 (contact strategy CALLBACK).

Auto-approves contact requests from whitelisted handles, rejects the rest. The
whitelist matches the QA requester agent (langgraph ``simple_agent``, handle
``nir.singhertest/qa-lg-simple-agent-...``), so F2 should auto-approve.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging

from _common import run_parlant_agent

from band.core.types import AdapterFeatures, Capability, Emit
from band.platform.event import ContactRequestReceivedEvent
from band.runtime.types import ContactEventConfig, ContactEventStrategy

logger = logging.getLogger(__name__)

# The QA requester is langgraph's simple_agent (see config.yaml contacts block).
APPROVED_HANDLE_PATTERNS = [
    "*qa-lg-simple-agent*",
    "nir.singhertest/qa-lg-*",
]

CONTACTS_DESCRIPTION = """You are a contact-management assistant on the Band platform.

When the user asks you to manage contacts — list, add, remove, list requests, or
respond to requests — use the appropriate contact tools.
"""


async def whitelist_approve(event: object, tools: object) -> None:
    if isinstance(event, ContactRequestReceivedEvent):
        handle = (event.payload.from_handle or "").lstrip("@")
        if any(fnmatch.fnmatch(handle, pat) for pat in APPROVED_HANDLE_PATTERNS):
            logger.info("Whitelist match — approving %s", handle)
            await tools.respond_contact_request("approve", request_id=event.payload.id)
        else:
            logger.info("Not on whitelist — rejecting %s", handle)
            await tools.respond_contact_request("reject", request_id=event.payload.id)


if __name__ == "__main__":
    asyncio.run(
        run_parlant_agent(
            "parlant_contacts_test",
            features=AdapterFeatures(
                capabilities={Capability.CONTACTS}, emit={Emit.EXECUTION}
            ),
            description=CONTACTS_DESCRIPTION,
            contact_config=ContactEventConfig(
                strategy=ContactEventStrategy.CALLBACK,
                on_event=whitelist_approve,
                broadcast_changes=True,
            ),
        )
    )
