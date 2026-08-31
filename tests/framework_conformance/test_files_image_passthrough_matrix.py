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

try:
    import crewai  # noqa: F401

    _CREWAI_AVAILABLE = True
except ImportError:
    _CREWAI_AVAILABLE = False

try:
    import pydantic_ai  # noqa: F401

    _PYDANTIC_AI_AVAILABLE = True
except ImportError:
    _PYDANTIC_AI_AVAILABLE = False

# crewai and pydantic-ai aren't both installed in every lane's venv (a
# three-way conflict group with parlant -- see docs/dependency-conflicts.md):
# dev-crewai lacks pydantic-ai, dev-parlant lacks both. These framework_ids
# need a per-lane skip the other probes (all in every lane's `dev` baseline)
# don't.
_SOMETIMES_MISSING: dict[str, bool] = {
    "crewai": _CREWAI_AVAILABLE,
    "crewai_flow": _CREWAI_AVAILABLE,
    "pydantic_ai": _PYDANTIC_AI_AVAILABLE,
}

_IMAGE_RESULT: dict[str, Any] = {
    "content": [{"type": "image", "data": "ZmFrZQ==", "mimeType": "image/png"}]
}


class _StubReadRoomFileTools:
    """Minimal AgentToolsProtocol double whose read_room_file/execute_tool_call
    always return the fixed image result -- only what each probe's dispatch
    path calls (MCP-based probes call read_room_file; generic-dispatch probes
    like agno call execute_tool_call)."""

    async def read_room_file(self, file_id: str) -> dict[str, Any]:
        del file_id
        return _IMAGE_RESULT

    async def execute_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del tool_name, arguments
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


async def _probe_langgraph() -> bool:
    from unittest.mock import AsyncMock, MagicMock

    from band.core.types import AdapterFeatures, Capability
    from band.integrations.langgraph.langchain_tools import agent_tools_to_langchain

    tools = MagicMock()
    tools.is_hub_room = False
    tools.execute_tool_call = AsyncMock(return_value=_IMAGE_RESULT)
    wrapped = {
        tool.name: tool
        for tool in agent_tools_to_langchain(
            tools,
            features=AdapterFeatures(capabilities=frozenset({Capability.FILES})),
        )
    }

    result = await wrapped["band_read_room_file"].ainvoke({"file_id": "file-1"})

    return result == [{"type": "image", "mime_type": "image/png", "base64": "ZmFrZQ=="}]


async def _probe_agno() -> bool:
    from agno.tools.function import ToolResult

    from band.adapters.agno import _bind_room_tools, _make_band_entrypoint

    entry = _make_band_entrypoint("band_read_room_file")
    with _bind_room_tools(_StubReadRoomFileTools()):
        result = await entry(file_id="file-1")

    return (
        isinstance(result, ToolResult)
        and result.images is not None
        and len(result.images) == 1
        and result.images[0].content == b"fake"
        and result.images[0].mime_type == "image/png"
    )


async def _probe_strands() -> bool:
    from band.adapters.strands import _tool_result

    tool_use = {"toolUseId": "t1", "name": "band_read_room_file", "input": {}}

    result = _tool_result(tool_use, value=_IMAGE_RESULT, ok=True)

    return result["content"] == [
        {"image": {"format": "png", "source": {"bytes": b"fake"}}}
    ]


async def _probe_copilot_sdk() -> bool:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from copilot import ToolInvocation

    from band.adapters.copilot_sdk import CopilotSDKAdapter
    from band.runtime.tools import ToolCallOutcome

    room_tools = MagicMock()
    room_tools.execute_tool_call_structured = AsyncMock(
        return_value=ToolCallOutcome(value=_IMAGE_RESULT, ok=True)
    )
    adapter = CopilotSDKAdapter.__new__(CopilotSDKAdapter)
    adapter.features = SimpleNamespace(emit=())
    adapter._custom_tools = []
    adapter._turn_state = {}
    adapter._room_tools = {"room-1": room_tools}

    result = await adapter._execute_bridged_tool(
        "room-1",
        ToolInvocation(
            tool_call_id="c1", tool_name="band_read_room_file", arguments={}
        ),
    )

    binary = result.binary_results_for_llm
    return (
        binary is not None
        and len(binary) == 1
        and binary[0].data == "ZmFrZQ=="
        and binary[0].mime_type == "image/png"
        and binary[0].type == "image"
    )


async def _probe_codex() -> bool:
    from band.adapters.codex import _image_content_items

    content_items = _image_content_items(_IMAGE_RESULT)

    return content_items == [
        {"type": "inputImage", "imageUrl": "data:image/png;base64,ZmFrZQ=="}
    ]


async def _probe_pydantic_ai() -> bool:
    from band.adapters.pydantic_ai import PydanticAIAdapter
    from band.core.types import Capability

    adapter = PydanticAIAdapter(model="test", capabilities=Capability.FILES)
    await adapter.on_started(agent_name="Probe", agent_description="probe")
    read_room_file = adapter._agent._function_toolset.tools["band_read_room_file"]

    from types import SimpleNamespace

    result = await read_room_file.function(
        SimpleNamespace(deps=_StubReadRoomFileTools()), file_id="file-1"
    )

    if not isinstance(result, list) or len(result) != 1:
        return False
    binary = result[0]
    return binary.data == b"fake" and binary.media_type == "image/png"


async def _probe_crewai() -> bool:
    from band.integrations.crewai.tools import (
        CrewAIToolContext,
        NoopReporter,
        build_band_crewai_tools,
        vision_sentinel,
    )
    from band.core.types import Capability

    context = CrewAIToolContext(room_id="room-1", tools=_StubReadRoomFileTools())
    tools = build_band_crewai_tools(
        get_context=lambda: context,
        reporter=NoopReporter(),
        capabilities=frozenset({Capability.FILES}),
    )
    read_room_file = next(t for t in tools if t.name == "band_read_room_file")

    result = read_room_file._run(file_id="file-1")

    return result == vision_sentinel(_IMAGE_RESULT)


IMAGE_PASSTHROUGH_PROBES: dict[str, Callable[[], Awaitable[bool]]] = {
    "claude_sdk": _probe_claude_sdk,
    "anthropic": _probe_anthropic,
    "opencode": _probe_opencode,
    "gemini": _probe_gemini,
    "langgraph": _probe_langgraph,
    "agno": _probe_agno,
    "strands": _probe_strands,
    "copilot_sdk": _probe_copilot_sdk,
    "codex": _probe_codex,
    "pydantic_ai": _probe_pydantic_ai,
    "crewai": _probe_crewai,
    # crewai_flow shares integrations/crewai/tools.py with crewai -- same probe.
    "crewai_flow": _probe_crewai,
}


def test_probe_registry_matches_supported_framework_ids() -> None:
    """The probe set is the live proof behind
    IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS -- the two must name exactly
    the same frameworks, or one of them has drifted from the other."""
    assert set(IMAGE_PASSTHROUGH_PROBES) == IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS


def _framework_param(framework_id: str) -> Any:
    available = _SOMETIMES_MISSING.get(framework_id, True)
    if not available:
        return pytest.param(
            framework_id,
            marks=pytest.mark.skip(reason=f"{framework_id} not installed in this venv"),
        )
    return pytest.param(framework_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "framework_id",
    [_framework_param(fid) for fid in sorted(IMAGE_PASSTHROUGH_PROBES)],
)
async def test_image_result_passes_through_as_real_content(framework_id: str) -> None:
    probe = IMAGE_PASSTHROUGH_PROBES[framework_id]

    assert await probe(), (
        f"{framework_id} did not pass an image band_read_room_file result "
        "through as real image content"
    )
