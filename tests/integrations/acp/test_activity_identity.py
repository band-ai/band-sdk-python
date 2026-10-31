"""ACP activity identity patches do not require invocation arguments."""

from __future__ import annotations

import pytest
from acp.schema import ToolCallUpdate

from band.integrations.acp.types import ACPToolActivity
from band.runtime.tools import BAND_MCP_SERVER_NAME, BandTool
from tests.integrations.acp.clientbehavior.kit import band_activity_metadata

TOOL = BandTool.GET_PARTICIPANTS
RAW_INVOCATION = {"server": BAND_MCP_SERVER_NAME, "tool": TOOL, "arguments": {}}


class TestActivityIdentity:
    @pytest.mark.parametrize(
        ("metadata", "raw_input", "expected"),
        [
            (
                band_activity_metadata(TOOL),
                None,
                ACPToolActivity("call", BAND_MCP_SERVER_NAME, TOOL),
            ),
            (None, RAW_INVOCATION, ACPToolActivity("call", BAND_MCP_SERVER_NAME, TOOL)),
            (
                {"mcp": None},
                RAW_INVOCATION,
                ACPToolActivity("call", mcp_metadata_rejected=True),
            ),
            ({"other": "metadata"}, None, ACPToolActivity("call")),
        ],
        ids=["metadata-only", "raw-identity", "rejected-metadata", "no-identity-patch"],
    )
    def test_identity_is_projected_without_invocation_data(
        self,
        metadata: dict[str, object] | None,
        raw_input: dict[str, object] | None,
        expected: ACPToolActivity,
    ) -> None:
        update = ToolCallUpdate(
            tool_call_id="call", field_meta=metadata, raw_input=raw_input
        )

        assert ACPToolActivity.from_acp(update) == expected
