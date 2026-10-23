"""Native permission records have identities independent of tool records."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import (
    OMP_PERMISSION_TOOL,
    OMP_TOOL_APPROVAL_HEADER,
    OmpAdapter,
    OmpAdapterConfig,
)
from band.converters.omp import OmpSessionState
from band.integrations.ompwire import OmpEventType, OmpUIMethod
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import (
    RepeatedRuntime,
    _cancel,
    _ready,
    owned_rpc_turn,
    tool_call_records,
    tool_result_records,
)
from tests.integrations.acp.conftest import make_platform_message


class TestPermissionRecords:
    @pytest.mark.parametrize(
        ("order", "expected"),
        [
            (
                "native-first",
                [OmpEventType.TOOL_START, OmpUIMethod.SELECT, OmpEventType.TOOL_END],
            ),
            (
                "permission-first",
                [OmpUIMethod.SELECT, OmpEventType.TOOL_START, OmpEventType.TOOL_END],
            ),
        ],
    )
    async def test_permission_and_native_records_are_independent_in_both_orders(
        self, monkeypatch, tmp_path: Path, order: str, expected: list[object]
    ) -> None:
        tools, observed = await owned_rpc_turn(
            monkeypatch, tmp_path, f"{OMP_TOOL_APPROVAL_HEADER}read", order
        )
        assert observed == expected
        calls = tool_call_records(tools)
        results = tool_result_records(tools)
        permission_calls = [
            call for call in calls if call["name"] == OMP_PERMISSION_TOOL
        ]
        permission_results = [
            result for result in results if result["name"] == OMP_PERMISSION_TOOL
        ]
        assert len(permission_calls) == len(permission_results) == 1
        permission_id = permission_calls[0]["tool_call_id"]
        assert permission_id != "ui-wire-id"
        assert permission_results[0]["tool_call_id"] == permission_id
        assert permission_results[0]["is_error"]
        assert [call for call in calls if call["name"] != OMP_PERMISSION_TOOL] == [
            {
                "name": "read",
                "args": {"path": "denied.txt"},
                "tool_call_id": "ui-wire-id",
            }
        ]
        assert [
            result for result in results if result["name"] != OMP_PERMISSION_TOOL
        ] == [
            {
                "name": "read",
                "output": "Native tool failed",
                "tool_call_id": "ui-wire-id",
                "is_error": True,
            }
        ]
        assert len(calls) == len(results) == 2

    async def test_repeated_owned_permission_requests_get_distinct_event_ids(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", RepeatedRuntime)
        monkeypatch.setattr("band.adapters.omp.omp_mcp_ready", _ready)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)), resolve_permission=_cancel
        )
        tools = FakeAgentTools()
        try:
            await adapter.on_message(
                make_platform_message("Repeat the question"),
                tools,
                OmpSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room",
            )
        finally:
            await adapter.cleanup_all()
        calls = tool_call_records(tools)
        permission_ids = [
            call["tool_call_id"]
            for call in calls
            if call["name"] == OMP_PERMISSION_TOOL
        ]
        assert len(permission_ids) == len(set(permission_ids)) == 2
        assert "ui-wire-id" not in permission_ids
        assert [call["tool_call_id"] for call in calls if call["name"] == "read"] == [
            "ui-wire-id"
        ]
