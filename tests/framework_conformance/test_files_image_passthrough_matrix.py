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

import base64
from collections.abc import Awaitable, Callable, Iterable
from types import SimpleNamespace
from typing import Any

import pytest

from band.runtime.tools import BandTool, TOOL_DEFINITIONS
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

# Derived, never re-typed: a probe asserting on a hand-written "ZmFrZQ==" would
# still pass if _IMAGE_RESULT changed underneath it.
_EXPECTED_BASE64: str = _IMAGE_RESULT["content"][0]["data"]
_EXPECTED_MIME_TYPE: str = _IMAGE_RESULT["content"][0]["mimeType"]
_EXPECTED_BYTES: bytes = base64.b64decode(_EXPECTED_BASE64)


def _is_expected_image(data: bytes | str, mime_type: str) -> bool:
    """Whether a framework's outgoing image block carries the probe's image.

    ``data`` arrives in whichever encoding that framework's SDK uses, so a
    probe passes what it found rather than normalising first.
    """
    expected = _EXPECTED_BYTES if isinstance(data, bytes) else _EXPECTED_BASE64
    return data == expected and mime_type == _EXPECTED_MIME_TYPE


def _tool_named(tools: Iterable[Any], name: str) -> Any:
    """The one tool a framework built for ``name``."""
    return next(tool for tool in tools if tool.name == name)


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
        tool_definitions=[TOOL_DEFINITIONS[BandTool.READ_ROOM_FILE]],
        get_tools=lambda _room_id: _StubReadRoomFileTools(),
        include_room_id=False,
    )
    handler = _tool_named(sdk_tools, BandTool.READ_ROOM_FILE).handler

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
            name=BandTool.READ_ROOM_FILE,
            input={"file_id": "file-1"},
        )
    ]

    results = await adapter._process_tool_calls(response, tools)

    content = results[0]["content"]
    if not isinstance(content, list) or len(content) != 1:
        return False
    block = content[0]
    return block["type"] == "image" and _is_expected_image(
        block["source"]["data"], block["source"]["media_type"]
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
    definition = TOOL_DEFINITIONS[BandTool.READ_ROOM_FILE]
    registration = build_tool_registration(
        definition,
        extend_with_chat_id(definition.input_model, None),
        resolver=resolver,
        strip_chat_id=True,
    )
    mcp = build_engine(EngineSpec(name="probe-opencode", tools=(registration,)))

    async with create_connected_server_and_client_session(mcp) as session:
        result = await session.call_tool(
            BandTool.READ_ROOM_FILE, {"chat_id": "room-1", "file_id": "file-1"}
        )

    if result.isError or len(result.content) != 1:
        return False
    block = result.content[0]
    return block.type == "image" and _is_expected_image(block.data, block.mimeType)


async def _probe_gemini() -> bool:
    from unittest.mock import AsyncMock, MagicMock

    from google.genai import types

    from band.adapters.gemini import GeminiAdapter

    adapter = GeminiAdapter(provider_key="test-key")
    tools = MagicMock()
    tools.execute_tool_call = AsyncMock(return_value=_IMAGE_RESULT)
    function_calls = [
        types.FunctionCall(
            name=BandTool.READ_ROOM_FILE, args={"file_id": "file-1"}, id="c1"
        )
    ]

    parts = await adapter._process_function_calls(function_calls, tools)

    function_response = parts[0].function_response
    if function_response is None or not function_response.parts:
        return False
    inline_data = function_response.parts[0].inline_data
    return inline_data is not None and _is_expected_image(
        inline_data.data, inline_data.mime_type
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

    result = await wrapped[BandTool.READ_ROOM_FILE].ainvoke({"file_id": "file-1"})

    if not isinstance(result, list) or len(result) != 1:
        return False
    block = result[0]
    return block["type"] == "image" and _is_expected_image(
        block["base64"], block["mime_type"]
    )


async def _probe_agno() -> bool:
    from agno.tools.function import ToolResult

    from band.adapters.agno import _bind_room_tools, _make_band_entrypoint

    entry = _make_band_entrypoint(BandTool.READ_ROOM_FILE)
    with _bind_room_tools(_StubReadRoomFileTools()):
        result = await entry(file_id="file-1")

    if not isinstance(result, ToolResult) or not result.images:
        return False
    image = result.images[0]
    return len(result.images) == 1 and _is_expected_image(
        image.content, image.mime_type
    )


async def _probe_strands() -> bool:
    from band.adapters.strands import _tool_result

    tool_use = {"toolUseId": "t1", "name": BandTool.READ_ROOM_FILE, "input": {}}

    result = _tool_result(tool_use, value=_IMAGE_RESULT, ok=True)

    # Strands names the format bare ("png"), not as a mime type.
    expected_format = _EXPECTED_MIME_TYPE.removeprefix("image/")
    return result["content"] == [
        {"image": {"format": expected_format, "source": {"bytes": _EXPECTED_BYTES}}}
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
            tool_call_id="c1", tool_name=BandTool.READ_ROOM_FILE, arguments={}
        ),
    )

    binary = result.binary_results_for_llm
    if not binary or len(binary) != 1:
        return False
    return binary[0].type == "image" and _is_expected_image(
        binary[0].data, binary[0].mime_type
    )


async def _probe_codex() -> bool:
    from band.adapters.codex import _image_content_items

    content_items = _image_content_items(_IMAGE_RESULT)

    data_uri = f"data:{_EXPECTED_MIME_TYPE};base64,{_EXPECTED_BASE64}"
    return content_items == [{"type": "inputImage", "imageUrl": data_uri}]


async def _probe_pydantic_ai() -> bool:
    from band.adapters.pydantic_ai import PydanticAIAdapter
    from band.core.types import Capability

    adapter = PydanticAIAdapter(model="test", capabilities=Capability.FILES)
    await adapter.on_started(agent_name="Probe", agent_description="probe")
    read_room_file = adapter._agent._function_toolset.tools[BandTool.READ_ROOM_FILE]

    result = await read_room_file.function(
        SimpleNamespace(deps=_StubReadRoomFileTools()), file_id="file-1"
    )

    if not isinstance(result, list) or len(result) != 1:
        return False
    binary = result[0]
    return _is_expected_image(binary.data, binary.media_type)


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
    read_room_file = _tool_named(tools, BandTool.READ_ROOM_FILE)

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
    """The probes are the live proof behind the supported-framework set."""
    assert set(IMAGE_PASSTHROUGH_PROBES) == IMAGE_PASSTHROUGH_SUPPORTED_FRAMEWORK_IDS


@pytest.mark.skipif(not _CREWAI_AVAILABLE, reason="crewai not installed in this venv")
def test_crewai_platform_tool_name_is_plain_str() -> None:
    """CrewAI's real BaseTool doesn't validate field defaults, so a bare
    BandTool (StrEnum) default would leave tool.name a BandTool instance at
    runtime instead of the str the field is typed as -- str(spec.name) at
    the PlatformTool definition must keep it a plain str."""
    from band.core.types import Capability
    from band.integrations.crewai.tools import (
        CrewAIToolContext,
        NoopReporter,
        build_band_crewai_tools,
    )

    context = CrewAIToolContext(room_id="room-1", tools=_StubReadRoomFileTools())
    tools = build_band_crewai_tools(
        get_context=lambda: context,
        reporter=NoopReporter(),
        capabilities=frozenset({Capability.FILES}),
    )

    for tool in tools:
        assert type(tool.name) is str, (
            f"{tool.name!r} is {type(tool.name)}, not plain str"
        )


def _framework_param(framework_id: str) -> Any:
    available = _SOMETIMES_MISSING.get(framework_id, True)
    return pytest.param(
        framework_id,
        marks=pytest.mark.skipif(
            not available, reason=f"{framework_id} not installed in this venv"
        ),
    )


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
