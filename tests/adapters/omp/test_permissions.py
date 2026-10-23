"""Native OMP approval choices and resolver boundaries."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OmpAdapter
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import OmpUIMethod, OmpUIRequest
from band.testing import FakeAgentTools


class TestPermissions:
    @pytest.mark.parametrize(
        ("method", "options", "expected_value", "expected_cancelled"),
        [
            (
                OmpUIMethod.SELECT,
                [OMP_FORM_APPROVE, OMP_FORM_DENY],
                OMP_FORM_APPROVE,
                False,
            ),
            (OmpUIMethod.SELECT, ["personal", "work"], None, True),
            ("notify", [OMP_FORM_APPROVE, OMP_FORM_DENY], None, True),
        ],
        ids=["default-approve", "unrelated-options", "non-select"],
    )
    @pytest.mark.asyncio
    async def test_permission_defaults_match_offered_native_choices(
        self, method, options, expected_value, expected_cancelled
    ):
        response = await OmpAdapter()._permission(
            OmpUIRequest(
                id="approval",
                method=method,
                title="Approve tool?",
                options=options,
            ),
            "room",
            "session",
            FakeAgentTools(),
        )
        assert response.value == expected_value
        assert response.cancelled is expected_cancelled

    @pytest.mark.asyncio
    async def test_permission_resolver_cannot_invent_an_unoffered_choice(self):
        adapter = OmpAdapter(resolve_permission=AsyncMock(return_value="Allow always"))
        response = await adapter._permission(
            OmpUIRequest(
                id="approval",
                method=OmpUIMethod.SELECT,
                title="Approve tool?",
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            ),
            "room",
            "session",
            FakeAgentTools(),
        )
        assert response.cancelled
        assert response.value is None
