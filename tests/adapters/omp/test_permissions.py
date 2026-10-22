"""Native OMP approval choices and resolver boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter, OmpTurn
from band.integrations.ompwire import OmpUIRequest
from band.testing import FakeAgentTools


class TestPermissions:
    @pytest.mark.asyncio
    async def test_permission_defaults_only_allow_native_approval_choices(self):
        adapter = OmpAdapter()
        approved = await adapter._permission(
            OmpUIRequest(
                id="approval",
                method="select",
                title="Approve tool?",
                options=["Approve", "Deny"],
            ),
            "room",
            "session",
            OmpTurn(tools=FakeAgentTools()),
        )
        unrelated = await adapter._permission(
            OmpUIRequest(
                id="question",
                method="select",
                title="Choose an account",
                options=["personal", "work"],
            ),
            "room",
            "session",
            OmpTurn(tools=FakeAgentTools()),
        )
        assert approved.value == "Approve"
        assert unrelated.cancelled

    @pytest.mark.asyncio
    async def test_permission_resolver_cannot_invent_an_unoffered_choice(self):
        adapter = OmpAdapter(resolve_permission=AsyncMock(return_value="Allow always"))
        response = await adapter._permission(
            OmpUIRequest(
                id="approval",
                method="select",
                title="Approve tool?",
                options=["Approve", "Deny"],
            ),
            "room",
            "session",
            OmpTurn(tools=FakeAgentTools()),
        )
        assert response.cancelled
        assert response.value is None
