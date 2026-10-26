"""Unencodable tool arguments retain identity and redact private fields."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import JsonValue

from band.adapters.omp import OmpAdapter, OmpAdapterConfig
from band.adapters.omp.toolprotocol import PRIVATE_PAYLOAD_UNAVAILABLE
from band.adapters.omp.turnstate import OmpTurn
from band.core.types import Emit
from band.integrations.ompwire import OmpEvent, OmpEventType
from band.testing import FakeAgentTools
from tests.adapters.omp.activitykit import tool_end
from tests.adapters.omp.records import tool_call_records
from tests.integrations.omppermissionpeer import NATIVE_EVENT_ID


class TestEncodeFallback:
    async def test_unencodable_arguments_keep_path_keys(self, tmp_path: Path) -> None:
        arguments: dict[str, JsonValue] = {
            "path": "visible.txt",
            "file_path": False,
            "target": None,
            "nested": {"secret": "private-envelope-bytes"},
            "count": 10**5000,
        }
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=False),
            emit={Emit.TOOL_CALLS},
        )
        tools = FakeAgentTools()
        turn = OmpTurn(tools=tools)
        limit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            await adapter._emit_event(
                OmpEvent(
                    type=OmpEventType.TOOL_START,
                    toolCallId=NATIVE_EVENT_ID,
                    toolName="read",
                    args=arguments,
                ),
                turn,
            )
            await adapter._emit_event(
                tool_end(NATIVE_EVENT_ID, "read", is_error=True), turn
            )
        finally:
            sys.set_int_max_str_digits(limit)
        (call,) = tool_call_records(tools)
        assert call["name"] == "read"
        assert call["tool_call_id"] == NATIVE_EVENT_ID
        assert call["args"] == {
            "path": "visible.txt",
            "file_path": False,
            "target": None,
            "arguments": PRIVATE_PAYLOAD_UNAVAILABLE,
        }
        assert "private-envelope-bytes" not in str(call)
