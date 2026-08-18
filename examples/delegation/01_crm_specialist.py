# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk[anthropic]"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""CRM specialist — an agent that acts with the ASKER's credentials (M2).

Bob owns and runs this agent. When ALICE @mentions it, the platform mints a
delegation envelope on the message (who is asking — INT-992); the agent's
custom tool exchanges that envelope for a short-lived CRM token that acts as
Alice — her consent, her provider connection — via
``ctx.credentials.token_for(<connector-id>)`` (INT-993/994).

The interesting part is the FIRST-RUN FUNNEL: on a fresh pairing the exchange
fails in a specific, typed order, and this tool renders each failure as
guidance the humans can act on:

    1. ``ConsentMissing``       -> Alice must grant this agent consent
    2. ``ProviderNotConnected`` -> Alice must connect the provider
    3. success                  -> a token acting as Alice

READ THE README FIRST: on a stock deployment the exchange endpoint is inert
(every call returns ``delegation_denied``) until the target connector's
``delegation_credential_mode`` is seeded to ``originator``/``any`` — that
field currently has no API/UI surface.

Run with (from repo root, as BOB's agent):
    CRM_CONNECTOR_ID=<mcp-connector-uuid> uv run examples/delegation/01_crm_specialist.py

Then, as ALICE (a different user, in the web app), @mention the agent and ask
it to look up a customer.
"""

from __future__ import annotations

import asyncio
import logging
import os

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from setup_logging import setup_logging
from band import Agent, ExecutionContext
from band.adapters import AnthropicAdapter
from band.platform import (
    ConsentMissing,
    DelegationError,
    NoDelegation,
    ProviderNotConnected,
)

setup_logging()
logger = logging.getLogger(__name__)

# The audience for the exchange: the MCP CONNECTOR ID (a UUID) that is on the
# asker's consent allowlist. Not a name, not a URL, not a scope.
CRM_CONNECTOR_ID = os.getenv("CRM_CONNECTOR_ID", "")


class LookupCustomerInput(BaseModel):
    """Look up a customer's record in the CRM, acting as the person who asked."""

    customer: str = Field(description="Customer name or account id to look up")


async def lookup_customer(
    args: LookupCustomerInput, ctx: ExecutionContext | None
) -> str:
    """Exchange the asker's delegation for a CRM token, or explain what's missing.

    Declaring the SECOND parameter is the INT-994 opt-in: the adapter threads
    the per-message ``ExecutionContext`` into it (``None`` if unavailable).
    ``ctx.delegation`` is the typed identity envelope; ``ctx.credentials`` is
    the per-message resolver — it caches per audience, respects the
    provider's ``expires_at`` (30 s skew, no reuse when null), and
    single-flights concurrent calls so a tool fan-out cannot burn the
    per-message exchange cap (default 20).
    """
    if ctx is None:
        return (
            "Tool context is unavailable in this adapter, so I cannot resolve "
            "delegated credentials."
        )

    envelope = ctx.delegation
    if envelope is None:
        return (
            "This message is owner-invoked — it carries no delegation "
            "envelope, so there is nobody to act on behalf of. Have a "
            "DIFFERENT user @mention me to see the delegated flow."
        )

    originator = envelope.originator
    asker = (originator.handle or originator.uuid) if originator else "the requester"

    if not CRM_CONNECTOR_ID:
        return (
            "CRM_CONNECTOR_ID is not set. Export the MCP connector id (a UUID "
            "from the asker's consent allowlist) and restart me."
        )

    try:
        token = await ctx.credentials.token_for(CRM_CONNECTOR_ID)
    except ConsentMissing as error:
        # First-run funnel, step 1 of 2. str(error) already carries the
        # contract's remediation ("Ask the user to grant consent, then retry.")
        return (
            f"I can't act on behalf of @{asker} yet: {error} "
            f"(@{asker}: grant it under Settings -> Agent consents, or via "
            "the /api/v1/me consent API, then ask me again.)"
        )
    except ProviderNotConnected as error:
        # First-run funnel, step 2 of 2: consent exists, connection doesn't.
        return (
            f"@{asker} has granted consent, but I still can't reach the CRM "
            f"as them: {error} "
            f"(@{asker}: connect the provider under Settings -> Connected "
            "apps, then ask me again.)"
        )
    except NoDelegation as error:
        # Client-side absence error (I3) — reachable when the envelope lost
        # its message_id; the exchange was never attempted.
        return str(error)
    except DelegationError as error:
        # Terminal for this message: window_expired, revoked,
        # audience_not_allowed, cross_org_blocked, delegation_denied,
        # rate_limited, the 404 family — nothing the humans can fix mid-turn.
        code = error.code or "delegation_error"
        return f"Delegated credentials are unavailable ({code}): {error}"

    acting_as = (
        token.obo.originator_handle
        if token.obo and token.obo.originator_handle
        else asker
    )
    expires = (
        token.expires_at.isoformat() if token.expires_at else "unknown (never reused)"
    )
    # NEVER put token.access_token in a message — it is a live provider
    # credential. Use it in an API call and let it expire.
    return (
        f"(demo) I hold a short-lived CRM credential acting as @{acting_as} "
        f"(type={token.token_type}, expires={expires}). A real tool would now "
        f"call the CRM API with it to look up {args.customer!r}; this demo "
        "stops at the exchange so the credential never leaves the process."
    )


async def main() -> None:
    load_dotenv()

    ws_url = os.getenv("BAND_WS_URL")
    rest_url = os.getenv("BAND_REST_URL")

    if not ws_url:
        raise ValueError("BAND_WS_URL environment variable is required")
    if not rest_url:
        raise ValueError("BAND_REST_URL environment variable is required")

    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        prompt=(
            "You are a CRM specialist agent owned by Bob. When someone asks "
            "about a customer, use the lookupcustomer tool. The tool acts "
            "with the ASKER's credentials; if it reports that consent or a "
            "provider connection is missing, relay its guidance verbatim — "
            "it tells the asker exactly what to do next."
        ),
        additional_tools=[(LookupCustomerInput, lookup_customer)],
    )

    agent = Agent.from_config(
        "crm_specialist",
        adapter=adapter,
        ws_url=ws_url,
        rest_url=rest_url,
    )

    logger.info("CRM specialist online — have another user @mention it.")
    await agent.run()


if __name__ == "__main__":
    asyncio.run(main())
