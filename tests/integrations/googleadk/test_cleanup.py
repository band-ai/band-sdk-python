"""Successful decline stops preserve normal ADK model-client cleanup."""

from __future__ import annotations

import httpx

from band.adapters.google_adk import GoogleADKAdapter
from band.testing import FakeAgentTools
from tests.framework_conformance.turnprobes import turn_input
from tests.integrations.googleadk.kit import DeclineInput, GoogleModelPeer, decline


class TestCleanup:
    async def test_successful_stop_awaits_real_external_client_closure(
        self, google_model: GoogleModelPeer, closed_clients: list[httpx.AsyncClient]
    ) -> None:
        google_model.decline_then_followup()
        adapter = GoogleADKAdapter(additional_tools=[(DeclineInput, decline)])
        await adapter.on_started("Agent", "An agent")
        await adapter.on_event(turn_input(FakeAgentTools()))

        assert closed_clients
        assert all(client.is_closed for client in closed_clients)
        assert len(google_model.requests) == 1
