"""Native approval choices cannot exceed the offered options."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OMP_PERMISSION_TOOL, OmpAdapter, OmpAdapterConfig
from band.adapters.omp.turnstate import OmpTurn
from band.core.types import Emit
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import OmpUIMethod, OmpUIRequest
from band.testing import FakeAgentTools
from tests.adapters.omp.records import tool_call_records


class TestPermissions:
    @pytest.mark.parametrize(
        (
            "method",
            "options",
            "expected_value",
            "expected_cancelled",
            "records_permission",
        ),
        [
            (
                OmpUIMethod.SELECT,
                [OMP_FORM_APPROVE, OMP_FORM_DENY],
                OMP_FORM_APPROVE,
                False,
                False,
            ),
            (OmpUIMethod.SELECT, ["personal", "work"], None, True, True),
            ("notify", [OMP_FORM_APPROVE, OMP_FORM_DENY], None, True, False),
        ],
        ids=["default-approve", "unrelated-options", "non-select"],
    )
    async def test_permission_defaults_match_offered_native_choices(
        self,
        tmp_path: Path,
        method: str,
        options: list[str],
        expected_value: str | None,
        expected_cancelled: bool,
        records_permission: bool,
    ) -> None:
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.TOOL_CALLS}
        )
        tools = FakeAgentTools()
        response = await adapter._permission(
            OmpUIRequest(
                id="approval", method=method, title="Approve tool?", options=options
            ),
            "room",
            "session",
            OmpTurn(tools=tools),
        )
        assert response.value == expected_value
        assert response.cancelled is expected_cancelled
        assert (
            OMP_PERMISSION_TOOL in [call["name"] for call in tool_call_records(tools)]
        ) is records_permission

    async def test_permission_resolver_cannot_invent_an_unoffered_choice(
        self, tmp_path: Path
    ) -> None:
        async def choose_unoffered(_request) -> str:
            return "Allow always"

        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_permission=choose_unoffered,
            emit={Emit.TOOL_CALLS},
        )
        response = await adapter._permission(
            OmpUIRequest(
                id="approval",
                method=OmpUIMethod.SELECT,
                title="Approve tool?",
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            ),
            "room",
            "session",
            OmpTurn(tools=FakeAgentTools()),
        )
        assert response.cancelled
        assert response.value is None
