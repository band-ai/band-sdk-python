"""Live matrix proving each IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS entry
actually passes an image band_read_room_file result through as real
vision/image content -- not just that the bookkeeping constant lists it.

Each probe drives the framework's real tool-dispatch code path with a fake
tools object whose read_room_file returns the exact MCP-image-content shape
AgentTools.read_room_file produces for a small previewable image, and asserts
the framework's own outgoing tool-result shape is a real image block rather
than a json.dumps'd text blob. Deeper framework-specific coverage (the
non-image degrade-to-text path, error handling, custom-tool interaction)
stays in each framework's own test file -- this matrix exists to answer one
question, uniformly, for every framework that claims support: does a real
image actually get through.

To add a framework here: write a probe, add it to IMAGE_PASSTHROUGH_PROBES,
and add the framework_id to IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS in
test_adapter_conformance.py -- test_probe_registry_matches_supported_framework_ids
fails loudly if the two ever name different frameworks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from band.runtime.tools import TOOL_DEFINITIONS
from tests.framework_conformance.test_adapter_conformance import (
    IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS,
)

_IMAGE_RESULT: dict[str, Any] = {
    "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
}


class _StubReadRoomFileTools:
    """Minimal AgentToolsProtocol double whose read_room_file always returns
    the fixed image result -- only what each probe's dispatch path calls."""

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        del file_id
        return _IMAGE_RESULT


async def _probe_claude_sdk() -> bool:
    from band.integrations.claude_sdk.tools import build_band_sdk_tools

    sdk_tools = build_band_sdk_tools(
        tool_definitions=[TOOL_DEFINITIONS["band_read_room_file"]],
        get_tools=lambda _room_id: _StubReadRoomFileTools(),
        include_room_id=False,
    )
    handler = next(t for t in sdk_tools if t.name == "band_read_room_file").handler

    result = await handler({"file_id": "file-1"})

    return result == _IMAGE_RESULT


async def _probe_anthropic() -> bool:
    from unittest.mock import AsyncMock, MagicMock

    from anthropic.types import ToolUseBlock

    from band.adapters.anthropic import AnthropicAdapter

    adapter = AnthropicAdapter(emit=())
    tools = MagicMock()
    tools.execute_tool_call = AsyncMock(return_value=_IMAGE_RESULT)
    response = MagicMock()
    response.content = [
        ToolUseBlock(
            type="tool_use",
            id="tool-1",
            name="band_read_room_file",
            input={"file_id": "file-1"},
        )
    ]

    results = await adapter._process_tool_calls(response, tools)

    content = results[0]["content"]
    return (
        isinstance(content, list)
        and len(content) == 1
        and content[0]["type"] == "image"
        and content[0]["source"]["data"] == "ZmFrZQ=="
    )


async def _probe_opencode() -> bool:
    from mcp.shared.memory import create_connected_server_and_client_session

    from band.integrations.mcp.engine import (
        EmbeddedResolver,
        EngineSpec,
        build_engine,
        build_tool_registration,
        extend_with_chat_id,
    )

    resolver = EmbeddedResolver(get_tools=lambda _chat_id: _StubReadRoomFileTools())
    definition = TOOL_DEFINITIONS["band_read_room_file"]
    registration = build_tool_registration(
        definition,
        extend_with_chat_id(definition.input_model, None),
        resolver=resolver,
        strip_chat_id=True,
    )
    mcp = build_engine(EngineSpec(name="probe-opencode", tools=(registration,)))

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            "band_read_room_file", {"chat_id": "room-1", "file_id": "file-1"}
        )

    return (
        not result.isError
        and len(result.content) == 1
        and result.content[0].type == "image"
        and result.content[0].data == "ZmFrZQ=="
    )


async def _probe_gemini() -> bool:
    from unittest.mock import AsyncMock, MagicMock

    from google.genai import types

    from band.adapters.gemini import GeminiAdapter

    adapter = GeminiAdapter(provider_key="test-key")
    tools = MagicMock()
    tools.execute_tool_call = AsyncMock(return_value=_IMAGE_RESULT)
    function_calls = [
        types.FunctionCall(
            name="band_read_room_file", args={"file_id": "file-1"}, id="c1"
        )
    ]

    parts = await adapter._process_function_calls(function_calls, tools)

    function_response = parts[0].function_response
    if function_response is None or not function_response.parts:
        return False
    inline_data = function_response.parts[0].inline_data
    return (
        inline_data is not None
        and inline_data.mime_type == "image/png"
        and inline_data.data == b"fake"
    )


IMAGE_PASSTHROUGH_PROBES: dict[str, Callable[[], Awaitable[bool]]] = {
    "claude_sdk": _probe_claude_sdk,
    "anthropic": _probe_anthropic,
    "opencode": _probe_opencode,
    "gemini": _probe_gemini,
}


def test_probe_registry_matches_supported_framework_ids() -> None:
    """The probe set is the live proof behind
    IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS -- the two must name exactly
    the same frameworks, or one of them has drifted from the other."""
    assert set(IMAGE_PASSTHROUGH_PROBES) == IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS


@pytest.mark.asyncio
@pytest.mark.parametrize("framework_id", sorted(IMAGE_PASSTHROUGH_PROBES))
async def test_image_result_passes_through_as_real_content(framework_id: str) -> None:
    probe = IMAGE_PASSTHROUGH_PROBES[framework_id]

    assert await probe(), (
        f"{framework_id} did not pass an image band_read_room_file result "
        "through as real image content"
    )
