"""claude_sdk surfaces bare tool names, not its MCP transport prefix.

claude_sdk exposes band + custom tools via an in-process MCP server, so the Claude
Agent SDK namespaces them ``mcp__band__<tool>``. The platform ``tool_call`` event and
the approval UX are cross-adapter, semantic records where every other adapter uses the
bare name, so the adapter strips its own server's prefix at those boundaries.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from band.adapters.claude_sdk import TOOL_SEARCH, ClaudeSDKAdapter
from tests.adapters.claude_sdk.helpers import ClaudeRoom
from tests.baseline.decisions import ModelDecision

OpenRoom = Callable[..., Awaitable[ClaudeRoom]]


def test_semantic_tool_name_strips_only_our_server_prefix() -> None:
    strip = ClaudeSDKAdapter._semantic_tool_name
    assert strip("mcp__band__lookup") == "lookup"
    assert strip("mcp__band__band_list_memories") == "band_list_memories"
    # A bare name (or a built-in like ToolSearch) is unchanged...
    assert strip("ToolSearch") == "ToolSearch"
    # ...and an external MCP server's tools stay namespaced.
    assert strip("mcp__other__thing") == "mcp__other__thing"


async def test_the_room_sees_bare_tool_names(claude_room: OpenRoom) -> None:
    room = await claude_room()
    room.claude.script(
        [ModelDecision.call(TOOL_SEARCH, query="band"), room.model_reply("hi")]
    )

    await room.send("hello")

    assert room.tool_call_names == [TOOL_SEARCH, "band_send_message"]
    assert set(room.tool_outputs) == {TOOL_SEARCH, "band_send_message"}
