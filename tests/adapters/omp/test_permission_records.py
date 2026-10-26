"""Native permission records have identities independent of tool records."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import (
    OMP_PERMISSION_CALL_ID_PREFIX,
    OMP_PERMISSION_TOOL,
    OMP_TOOL_APPROVAL_HEADER,
)
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.ompwire import OmpEventType, OmpUIMethod, OmpUIRequest
from tests.adapters.omp.activitykit import tool_end, tool_start
from tests.adapters.omp.permissions import decline_permission, owned_rpc_turn
from tests.adapters.omp.records import tool_call_records, tool_result_records
from tests.adapters.omp.turns import NativeTurnFactory
from tests.integrations.omppermissionpeer import (
    DENIED_PATH,
    NATIVE_EVENT_ID,
    NATIVE_FAILURE_OUTPUT,
    PermissionOrder,
)


class TestPermissionRecords:
    @pytest.mark.parametrize(
        ("order", "expected"),
        [
            (
                PermissionOrder.NATIVE_FIRST,
                [OmpEventType.TOOL_START, OmpUIMethod.SELECT, OmpEventType.TOOL_END],
            ),
            (
                PermissionOrder.PERMISSION_FIRST,
                [OmpUIMethod.SELECT, OmpEventType.TOOL_START, OmpEventType.TOOL_END],
            ),
        ],
    )
    async def test_permission_and_native_records_are_independent_in_both_orders(
        self,
        monkeypatch,
        tmp_path: Path,
        order: PermissionOrder,
        expected: list[object],
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
        assert permission_id.startswith(OMP_PERMISSION_CALL_ID_PREFIX)
        assert permission_id != NATIVE_EVENT_ID
        assert permission_results[0]["tool_call_id"] == permission_id
        assert permission_results[0]["is_error"]
        assert [call for call in calls if call["name"] != OMP_PERMISSION_TOOL] == [
            {
                "name": "read",
                "args": {"path": DENIED_PATH},
                "tool_call_id": NATIVE_EVENT_ID,
            }
        ]
        assert [
            result for result in results if result["name"] != OMP_PERMISSION_TOOL
        ] == [
            {
                "name": "read",
                "output": NATIVE_FAILURE_OUTPUT,
                "tool_call_id": NATIVE_EVENT_ID,
                "is_error": True,
            }
        ]
        assert len(calls) == len(results) == 2

    async def test_repeated_owned_permission_requests_get_distinct_event_ids(
        self, native_turn: NativeTurnFactory
    ) -> None:
        with pytest.raises(TurnResultAlreadyReported):
            async with native_turn(resolve_permission=decline_permission) as turn:
                request = OmpUIRequest(
                    id=NATIVE_EVENT_ID, method=OmpUIMethod.SELECT, title="A question"
                )
                first = await turn.callbacks.ui(request)
                second = await turn.callbacks.ui(request)
                assert first.cancelled and second.cancelled
                await turn.callbacks.event(
                    tool_start(NATIVE_EVENT_ID, "read", path=DENIED_PATH)
                )
                await turn.callbacks.event(
                    tool_end(NATIVE_EVENT_ID, "read", is_error=True)
                )
        tools = turn.tools
        calls = tool_call_records(tools)
        permission_ids = [
            call["tool_call_id"]
            for call in calls
            if call["name"] == OMP_PERMISSION_TOOL
        ]
        assert len(permission_ids) == len(set(permission_ids)) == 2
        assert all(
            item.startswith(OMP_PERMISSION_CALL_ID_PREFIX) for item in permission_ids
        )
        assert NATIVE_EVENT_ID not in permission_ids
        assert [call["tool_call_id"] for call in calls if call["name"] == "read"] == [
            NATIVE_EVENT_ID
        ]
