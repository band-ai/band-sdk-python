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
from band.adapters.omp.turnstate import OmpTurn
from band.core.types import Emit
from band.integrations.omp import OMP_FORM_APPROVE, OMP_FORM_DENY
from band.integrations.ompwire import OmpUIMethod, OmpUIRequest
from band.testing import FakeAgentTools
from tests.adapters.omp.permissions import decline_permission
from tests.adapters.omp.records import tool_call_records


class TestApprovalTitles:
    @pytest.mark.asyncio
    async def test_path_and_content_details_are_projected(self, tmp_path: Path):
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
        adapter = OmpAdapter(
            OmpAdapterConfig(cwd=str(tmp_path)),
            resolve_permission=decline_permission,
            emit={Emit.TOOL_CALLS},
        )
        tools = FakeAgentTools()
        await adapter._permission(
            OmpUIRequest(
                id="approval",
                method=OmpUIMethod.SELECT,
                title=title,
                options=[OMP_FORM_APPROVE, OMP_FORM_DENY],
            ),
            "room",
            "session",
            OmpTurn(tools=tools),
        )
        permission = next(
            call
            for call in tool_call_records(tools)
            if call["name"] == OMP_PERMISSION_TOOL
        )
        assert secret not in json.dumps(permission)
        assert permission["args"]["tool_name"] == "band_send_room_file"
        assert permission["args"]["arguments"]["filename"] == "private.txt"
        assert permission["args"]["arguments"]["content"] != secret
