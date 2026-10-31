"""An MCP call identified in a tool call's ``_meta`` resolves strictly."""

from __future__ import annotations

from functools import partial
from typing import Any

import pytest
from acp.helpers import start_tool_call

from band.integrations.acp.types import ACPToolCall
from band.runtime.tools import BandTool, canonicalize_mcp_tool_name

ARGUMENTS = {"content": "The answer."}
TITLE = f"band_{BandTool.SEND_MESSAGE}"
CANONICALIZE = partial(canonicalize_mcp_tool_name, own_names={BandTool.SEND_MESSAGE})


def tool_call(meta: Any, raw_input: Any) -> ACPToolCall:
    """The normalized call for one ``tool_call`` frame carrying ``meta``."""
    frame = start_tool_call("tc-1", TITLE, raw_input=raw_input).model_copy(
        update={"field_meta": meta}
    )
    return ACPToolCall.from_acp(frame, canonicalize=CANONICALIZE)


class TestMetaMcpInvocation:
    def test_meta_identity_resolves_to_the_canonical_tool_and_its_arguments(
        self,
    ) -> None:
        """The arguments are the nested ``rawInput.arguments``, never the flat copy."""
        call = tool_call(
            {"mcp": {"server": "band", "tool": BandTool.SEND_MESSAGE}},
            {"arguments": ARGUMENTS, "content": "A flat copy."},
        )

        assert call == ACPToolCall(
            tool_call_id="tc-1",
            name=BandTool.SEND_MESSAGE,
            arguments=ARGUMENTS,
            mcp_server="band",
            mcp_tool=BandTool.SEND_MESSAGE,
            title=TITLE,
        )

    @pytest.mark.parametrize(
        ("meta", "raw_input", "metadata_rejected"),
        [
            (None, {"arguments": ARGUMENTS}, False),
            ({"mcp": "band"}, {"arguments": ARGUMENTS}, True),
            ({"mcp": {"tool": BandTool.SEND_MESSAGE}}, {"arguments": ARGUMENTS}, True),
            (
                {"mcp": {"server": 1, "tool": BandTool.SEND_MESSAGE}},
                {"arguments": ARGUMENTS},
                True,
            ),
            ({"mcp": {"server": "band"}}, {"arguments": ARGUMENTS}, True),
            ({"mcp": {"server": "band", "tool": 1}}, {"arguments": ARGUMENTS}, True),
            (
                {"mcp": {"server": "band", "tool": BandTool.SEND_MESSAGE}},
                ARGUMENTS,
                True,
            ),
            (
                {"mcp": {"server": "band", "tool": BandTool.SEND_MESSAGE}},
                {"arguments": "The answer."},
                True,
            ),
        ],
        ids=[
            "no-meta",
            "mcp-not-an-object",
            "no-server",
            "server-not-a-string",
            "no-tool",
            "tool-not-a-string",
            "no-nested-arguments",
            "arguments-not-an-object",
        ],
    )
    def test_an_incomplete_shape_keeps_the_reported_title_and_input(
        self, meta: Any, raw_input: dict[str, Any], metadata_rejected: bool
    ) -> None:
        call = tool_call(meta, raw_input)

        assert call == ACPToolCall(
            tool_call_id="tc-1",
            name=TITLE,
            arguments=raw_input,
            mcp_server=None,
            title=TITLE,
            mcp_metadata_rejected=metadata_rejected,
        )
