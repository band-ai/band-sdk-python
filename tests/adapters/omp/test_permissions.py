"""Native OMP approval choices and resolver boundaries."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from band.adapters.omp import OMP_PERMISSION_TOOL, OmpAdapter, OmpAdapterConfig
from band.core.types import Emit
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import OmpUIMethod, OmpUIRequest
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import UiRequestRuntime, run_turn, tool_call_records


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
    @pytest.mark.asyncio
    async def test_permission_defaults_match_offered_native_choices(
        self,
        monkeypatch,
        tmp_path: Path,
        method,
        options,
        expected_value,
        expected_cancelled,
        records_permission,
    ):
        runtime = UiRequestRuntime(
            OmpUIRequest(
                id="approval",
                method=method,
                title="Approve tool?",
                options=options,
            )
        )
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), emit={Emit.TOOL_CALLS}
        )
        tools = FakeAgentTools()
        try:
            await run_turn(adapter, tools)
            assert runtime.response is not None
            assert runtime.response.value == expected_value
            assert runtime.response.cancelled is expected_cancelled
            names = [call["name"] for call in tool_call_records(tools)]
            assert (OMP_PERMISSION_TOOL in names) is records_permission
        finally:
            await adapter.cleanup_all()

    @pytest.mark.asyncio
    async def test_permission_resolver_cannot_invent_an_unoffered_choice(
        self, monkeypatch, tmp_path: Path
    ):
        runtime = UiRequestRuntime(
            OmpUIRequest(
                id="approval",
                method=OmpUIMethod.SELECT,
                title="Approve tool?",
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            )
        )
        runtime.finish.set()
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_permission=AsyncMock(return_value="Allow always"),
            emit={Emit.TOOL_CALLS},
        )
        tools = FakeAgentTools()
        try:
            await run_turn(adapter, tools)
            assert runtime.response is not None
            assert runtime.response.cancelled
            assert runtime.response.value is None
        finally:
            await adapter.cleanup_all()
