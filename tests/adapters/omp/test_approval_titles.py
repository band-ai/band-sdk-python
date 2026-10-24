"""Live formatApprovalPrompt titles are projected and redacted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from band.adapters.omp import (
    OMP_PERMISSION_TOOL,
    OMP_TOOL_APPROVAL_HEADER,
    OmpAdapter,
    OmpAdapterConfig,
)
from band.converters.omp import OmpSessionState
from band.core.types import Emit
from band.testing import FakeAgentTools
from tests.adapters.omp.kit import LiveTitleRuntime, _cancel, tool_call_records
from tests.integrations.acp.conftest import make_platform_message


class TestApprovalTitles:
    @pytest.mark.asyncio
    async def test_path_and_content_details_are_projected(
        self, monkeypatch, tmp_path: Path
    ):
        secret = "private-file-bytes-live-title"
        title = (
            f"{OMP_TOOL_APPROVAL_HEADER}write\n"
            "Origin: MCP server tool\n"
            "Path: xd://mcp__band_send_room_file\n"
            "Content:\n"
            + json.dumps(
                {
                    "filename": "private.txt",
                    "content": secret,
                    "mentions": ["user"],
                }
            )
        )
        runtime = LiveTitleRuntime(title)
        monkeypatch.setattr("band.adapters.omp.OmpRpcClient", lambda *a, **kw: runtime)
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_permission=_cancel,
            emit={Emit.TOOL_CALLS},
        )
        tools = FakeAgentTools()
        try:
            await adapter.on_message(
                make_platform_message("Handle the permission request"),
                tools,
                OmpSessionState(),
                None,
                None,
                is_session_bootstrap=True,
                room_id="room",
            )
        finally:
            await adapter.cleanup_all()
        permission = next(
            call
            for call in tool_call_records(tools)
            if call["name"] == OMP_PERMISSION_TOOL
        )
        assert secret not in json.dumps(permission)
        assert permission["args"]["tool_name"] == "band_send_room_file"
        assert permission["args"]["arguments"]["filename"] == "private.txt"
        assert permission["args"]["arguments"]["content"] != secret
