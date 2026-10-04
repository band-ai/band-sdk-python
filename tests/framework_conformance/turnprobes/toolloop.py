"""Turn-outcome probes for the in-process tool-loop adapters; see ``turnprobes``.

Each runs the script through the adapter's own Band tool surface on its
existing scripted model. None of these adapters relay the model's final text.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.turnprobes import (
    TurnOutcomeProbe,
    TurnScript,
    turn_input,
)


async def run_strands(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("strands")
    from band.adapters.strands import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        StrandsAdapter,
    )
    from band.testing import (  # noqa: PLC0415 -- strands extra, absent from some lane venvs
        ScriptedStrandsModel,
        TextTurn,
        ToolTurn,
    )

    turns: list[Any] = [
        ToolTurn(call.name, call.arguments) for call in script.tool_calls
    ]
    if script.final_text:
        turns.append(TextTurn(script.final_text))
    adapter = StrandsAdapter(llm=ScriptedStrandsModel(turns))
    await adapter.on_started("Agent", "A test agent")
    await adapter.on_event(turn_input(tools))


async def run_pydantic_ai(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.function import (  # noqa: PLC0415 -- pydantic_ai extra, absent from some lane venvs
        AgentInfo,
        DeltaToolCall,
        FunctionModel,
    )

    from band.adapters.pydantic_ai import (  # noqa: PLC0415 -- pydantic_ai extra, absent from some lane venvs
        PydanticAIAdapter,
        PydanticAIAdapterConfig,
    )

    pending = list(script.tool_calls)

    async def stream(
        messages: list[Any], info: AgentInfo
    ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
        if pending:
            call = pending.pop(0)
            yield {
                0: DeltaToolCall(name=call.name, json_args=json.dumps(call.arguments))
            }
        else:
            yield script.final_text

    adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="test"))
    await adapter.on_started("Agent", "A test agent")
    assert adapter._agent is not None
    with adapter._agent.override(model=FunctionModel(stream_function=stream)):
        await adapter.on_event(turn_input(tools))


async def run_agno(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("agno")
    from agno.run.agent import (  # noqa: PLC0415 -- agno extra, absent from some lane venvs
        RunOutput,
    )

    from band.adapters.agno import (  # noqa: PLC0415 -- agno extra, absent from some lane venvs
        AgnoAdapter,
    )
    from tests.adapters.agno.helpers import (  # noqa: PLC0415 -- agno extra, absent from some lane venvs
        fake_agno_agent,
    )

    async def model_turn() -> None:
        # The adapter's Agno entrypoint for a Band tool is exactly this call
        # on the room's bound tools.
        for call in script.tool_calls:
            await tools.execute_tool_call(call.name, call.arguments)

    agent = fake_agno_agent(
        response=RunOutput(content=script.final_text), model_turn=model_turn
    )
    adapter = AgnoAdapter(agent=agent, emit=())
    await adapter.on_started("Agent", "A test agent")
    await adapter.on_event(turn_input(tools))


class ScriptedGraph:
    """A graph whose run calls the script's Band tools, as an agent graph would."""

    def __init__(self, script: TurnScript, turn_tools: list[Any]) -> None:
        self._script = script
        self._tools_by_name = {tool.name: tool for tool in turn_tools}

    async def astream_events(
        self, graph_input: dict[str, Any], **kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        for call in self._script.tool_calls:
            await self._tools_by_name[call.name].ainvoke(call.arguments)
        return
        yield


async def run_langgraph(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("langgraph")
    from band.adapters.langgraph import (  # noqa: PLC0415 -- langgraph extra, absent from some lane venvs
        LangGraphAdapter,
    )

    adapter = LangGraphAdapter(
        graph_factory=lambda turn_tools: ScriptedGraph(script, turn_tools)
    )
    await adapter.on_started("Agent", "A test agent")
    await adapter.on_event(turn_input(tools))


async def run_anthropic(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("anthropic")
    from anthropic.types import (  # noqa: PLC0415 -- anthropic extra, absent from some lane venvs
        Message,
        TextBlock,
        ToolUseBlock,
        Usage,
    )

    from band.adapters.anthropic import (  # noqa: PLC0415 -- anthropic extra, absent from some lane venvs
        AnthropicAdapter,
    )

    def response(content: list[Any], stop_reason: str) -> Message:
        return Message(
            id="msg",
            type="message",
            role="assistant",
            model="scripted",
            content=content,
            stop_reason=stop_reason,
            usage=Usage(input_tokens=0, output_tokens=0),
        )

    responses = [
        response(
            [
                ToolUseBlock(
                    type="tool_use",
                    id=f"tool-{index}",
                    name=call.name,
                    input=call.arguments,
                )
            ],
            "tool_use",
        )
        for index, call in enumerate(script.tool_calls)
    ]
    final = (
        [TextBlock(type="text", text=script.final_text)] if script.final_text else []
    )
    responses.append(response(final, "end_turn"))

    adapter = AnthropicAdapter()
    await adapter.on_started("Agent", "A test agent")
    with patch.object(adapter, "_call_anthropic", AsyncMock(side_effect=responses)):
        await adapter.on_event(turn_input(tools))


async def run_gemini(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("google.genai")
    from google.genai import (  # noqa: PLC0415 -- gemini extra, absent from some lane venvs
        types,
    )

    from band.adapters.gemini import (  # noqa: PLC0415 -- gemini extra, absent from some lane venvs
        GeminiAdapter,
    )

    def response(part: types.Part) -> types.GenerateContentResponse:
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=[part]))
            ]
        )

    responses = [
        response(types.Part.from_function_call(name=call.name, args=call.arguments))
        for call in script.tool_calls
    ]
    responses.append(response(types.Part.from_text(text=script.final_text)))

    adapter = GeminiAdapter()
    await adapter.on_started("Agent", "A test agent")
    with patch.object(adapter, "_call_gemini", AsyncMock(side_effect=responses)):
        await adapter.on_event(turn_input(tools))


async def run_google_adk(script: TurnScript, tools: FakeAgentTools) -> None:
    pytest.importorskip("google.adk")
    from band.adapters.google_adk import (  # noqa: PLC0415 -- google_adk extra, absent from some lane venvs
        GoogleADKAdapter,
        _get_tool_bridge_class,
    )

    tool_bridge = _get_tool_bridge_class()

    def scripted_runner(turn_tools: Any) -> MagicMock:
        """An ADK runner whose tool loop calls the script through the adapter's
        own tool bridge."""

        async def run_async(**kwargs: Any) -> AsyncIterator[Any]:
            for call in script.tool_calls:
                bridge = tool_bridge(
                    tool_name=call.name,
                    tool_description=call.name,
                    parameters_schema={},
                    tools=turn_tools,
                    custom_tools=[],
                )
                await bridge.run_async(args=call.arguments, tool_context=MagicMock())
            return
            yield

        runner = MagicMock()
        runner.session_service.create_session = AsyncMock()
        runner.run_async = run_async
        runner.close = AsyncMock()
        return runner

    adapter = GoogleADKAdapter()
    await adapter.on_started("Agent", "A test agent")
    with patch.object(adapter, "_create_runner", side_effect=scripted_runner):
        await adapter.on_event(turn_input(tools))


PROBES: dict[str, TurnOutcomeProbe] = {
    "strands": TurnOutcomeProbe(run=run_strands),
    "pydantic_ai": TurnOutcomeProbe(run=run_pydantic_ai),
    "agno": TurnOutcomeProbe(run=run_agno),
    "langgraph": TurnOutcomeProbe(run=run_langgraph),
    "anthropic": TurnOutcomeProbe(run=run_anthropic),
    "gemini": TurnOutcomeProbe(run=run_gemini),
    "google_adk": TurnOutcomeProbe(run=run_google_adk),
}
