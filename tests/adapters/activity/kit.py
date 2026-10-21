"""Model entry points that drive one Band tool call through a real framework boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.shared.memory import create_connected_server_and_client_session
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from band.core.protocols import AgentToolsProtocol
from band.core.types import AdapterFeatures
from band.integrations.pydantic_ai.tools import build_band_pydantic_ai_tools
from band.runtime.tools import AgentTools
from band.runtime.tools.registry import TOOL_DEFINITIONS
from tests.runtime.helpers import ROOM_ID
from tests.runtime.toolactivity.kit import band_tool_server

ModelCall = Callable[[AgentTools, str, dict[str, Any]], Awaitable[Any]]


async def call_over_mcp(tools: AgentTools, name: str, arguments: dict[str, Any]) -> Any:
    server = band_tool_server(tools, TOOL_DEFINITIONS[name])
    async with create_connected_server_and_client_session(server) as session:
        result = await session.call_tool(name, {**arguments, "chat_id": ROOM_ID})
    return result.content


async def call_over_pydantic_ai(
    tools: AgentTools, name: str, arguments: dict[str, Any]
) -> Any:
    """Run a real ``Agent`` whose model calls ``name`` once, then answers."""

    def model(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=arguments)])
        return ModelResponse(parts=[TextPart("done")])

    agent = Agent(
        FunctionModel(model),
        deps_type=AgentToolsProtocol,
        tools=build_band_pydantic_ai_tools(AdapterFeatures()),
    )
    result = await agent.run("go", deps=tools)
    return result.all_messages()


async def call_through_execute_tool_call(
    tools: AgentTools, name: str, arguments: dict[str, Any]
) -> Any:
    return await tools.execute_tool_call(name, arguments)


MODEL_CALLS = [call_over_mcp, call_over_pydantic_ai, call_through_execute_tool_call]
