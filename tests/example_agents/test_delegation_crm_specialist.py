"""Offline tests for the two-user delegation example (INT-995).

The example is the SDK's demonstration of the M2 first-run funnel: its custom
tool must opt in to the INT-994 ctx parameter, exchange via
``ctx.credentials`` (INT-993), and render each funnel error as guidance a
human can act on. These tests drive the handler with fakes — no platform, no
Anthropic — and pin the README's inert-endpoint precondition (contract caveat
1) so it cannot be silently dropped.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from band.core.delegation import DelegationEnvelope
from band.platform.delegation_exchange import (
    ConsentMissing,
    DelegatedToken,
    DelegationDenied,
    NoDelegation,
    ProviderNotConnected,
)
from band.runtime.custom_tools import custom_tool_accepts_ctx
from tests.loaders import load_script_module
from tests.paths import REPO_ROOT

pytest.importorskip("anthropic", reason="example uses the anthropic extra")

crm = load_script_module(
    "examples/delegation/01_crm_specialist.py", "delegation_crm_specialist"
)

ENVELOPE = DelegationEnvelope.model_validate(
    {
        "version": 1,
        "originator": {"uuid": "alice-uuid", "handle": "alice"},
        "message_id": "7e2a9c04-1111-2222-3333-444455556666",
        "minted_at": "2026-08-14T00:00:00Z",
    }
)


class FakeResolver:
    def __init__(self, outcome):
        self._outcome = outcome
        self.requested: list[str] = []

    async def token_for(self, audience: str):
        self.requested.append(audience)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


def make_ctx(outcome, envelope=ENVELOPE):
    return SimpleNamespace(delegation=envelope, credentials=FakeResolver(outcome))


def make_args():
    return crm.LookupCustomerInput(customer="ACME-042")


async def run_handler(ctx, monkeypatch=None):
    return await crm.lookup_customer(make_args(), ctx)


class TestHandlerContract:
    def test_handler_opts_in_to_the_ctx_parameter(self):
        assert custom_tool_accepts_ctx(crm.lookup_customer)

    async def test_success_reports_who_the_token_acts_as(self, monkeypatch):
        monkeypatch.setattr(
            crm, "CRM_CONNECTOR_ID", "11112222-3333-4444-5555-666677778888"
        )
        token = DelegatedToken.model_validate(
            {
                "access_token": "secret-token",
                "token_type": "bearer",
                "expires_at": "2026-08-14T01:00:00Z",
                "obo": {
                    "originator_uuid": "alice-uuid",
                    "originator_handle": "alice",
                    "agent_uuid": "agent-1",
                    "audience": "11112222-3333-4444-5555-666677778888",
                },
            }
        )
        ctx = make_ctx(token)

        reply = await run_handler(ctx)

        assert "alice" in reply
        assert "ACME-042" in reply
        # The provider credential itself must never be echoed into the room.
        assert "secret-token" not in reply
        assert ctx.credentials.requested == ["11112222-3333-4444-5555-666677778888"]

    async def test_owner_invoked_message_is_explained(self):
        ctx = SimpleNamespace(delegation=None, credentials=FakeResolver(None))

        reply = await crm.lookup_customer(make_args(), ctx)

        assert "owner" in reply.lower() or "envelope" in reply.lower()
        assert ctx.credentials.requested == []


class TestFirstRunFunnel:
    async def test_consent_missing_renders_grant_guidance(self, monkeypatch):
        monkeypatch.setattr(crm, "CRM_CONNECTOR_ID", "conn-id")
        error = ConsentMissing("No consent for this agent")
        reply = await run_handler(make_ctx(error))

        assert "alice" in reply
        # The contract's remediation string must reach the human.
        assert "grant consent" in reply.lower()

    async def test_provider_not_connected_renders_connect_guidance(self, monkeypatch):
        monkeypatch.setattr(crm, "CRM_CONNECTOR_ID", "conn-id")
        error = ProviderNotConnected("No usable connection")
        reply = await run_handler(make_ctx(error))

        assert "connect the provider" in reply.lower()
        # The example's own funnel rendering, not just the error passthrough:
        # it addresses the asker by handle.
        assert "alice" in reply

    async def test_no_delegation_is_rendered_not_raised(self, monkeypatch):
        monkeypatch.setattr(crm, "CRM_CONNECTOR_ID", "conn-id")
        error = NoDelegation("This message carries no delegation envelope")
        reply = await run_handler(make_ctx(error))

        assert "envelope" in reply.lower()

    async def test_terminal_errors_surface_their_code(self, monkeypatch):
        monkeypatch.setattr(crm, "CRM_CONNECTOR_ID", "conn-id")
        error = DelegationDenied("Delegation denied", status=403)
        reply = await run_handler(make_ctx(error))

        assert "delegation_denied" in reply


class TestReadmePrecondition:
    """Contract caveat 1 is binding: the README must state the inert-endpoint
    precondition — stock deployments answer delegation_denied until the
    connector opt-in surface ships."""

    def test_readme_states_the_seeded_connector_precondition(self):
        readme = (REPO_ROOT / "examples" / "delegation" / "README.md").read_text()

        assert "delegation_credential_mode" in readme
        assert "delegation_denied" in readme
        # The default that makes the exchange inert, and the two modes that arm it.
        assert "owner" in readme
        assert "originator" in readme
