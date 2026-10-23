"""Unencodable native tool arguments keep identity and redact private fields."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import JsonValue

from band.adapters.omp import PRIVATE_PAYLOAD_UNAVAILABLE, OmpAdapter, OmpAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import UnencodableCallRuntime, run_turn, tool_call_records
from tests.integrations.omppermissionpeer import NATIVE_EVENT_ID


class TestEncodeFallback:
    @pytest.mark.asyncio
    async def test_unencodable_arguments_keep_path_keys(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        arguments: dict[str, JsonValue] = {
            "path": "visible.txt",
            "file_path": False,
            "target": None,
            "nested": {"secret": "private-envelope-bytes"},
            "count": 10**5000,
        }
        runtime = UnencodableCallRuntime(arguments)
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path), inject_band_tools=False),
            emit={Emit.TOOL_CALLS},
        )
        tools = FakeAgentTools()
        limit = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            with pytest.raises(TurnResultAlreadyReported):
                await run_turn(adapter, tools)
        finally:
            sys.set_int_max_str_digits(limit)
            await adapter.cleanup_all()
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
