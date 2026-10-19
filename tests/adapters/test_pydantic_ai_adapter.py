"""Initialization behavior for Pydantic AI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel
from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from band.adapters.pydantic_ai import (
    PydanticAIAdapter,
    PydanticAIAdapterConfig,
    _drop_non_replayable_messages,
)
from band.runtime.custom_tools import get_custom_tool_name
from band.testing import FakeAgentTools
from tests.adapters.pydanticai.kit import (
    instrument_all_restored,
    mock_pydantic_agent,
    mock_tools,
    sample_message,
    trace_capture,
)


class TestInitialization:
    """Tests for adapter initialization."""

    @pytest.mark.asyncio
    async def test_agent_runs_the_configured_model_name(self):
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="test"))
        await adapter.on_started(agent_name="Bot", agent_description="")

        assert isinstance(adapter._agent.model, TestModel)

    @pytest.mark.asyncio
    async def test_agent_runs_the_live_llm(self):
        llm = TestModel()
        adapter = PydanticAIAdapter(llm=llm)
        await adapter.on_started(agent_name="Bot", agent_description="")

        assert adapter._agent.model is llm

    @pytest.mark.parametrize(
        ("config", "llm"),
        [
            (None, None),
            (PydanticAIAdapterConfig(model="test"), TestModel()),
        ],
        ids=["neither", "both"],
    )
    def test_requires_exactly_one_model_source(self, config, llm):
        with pytest.raises(ValueError, match="Set exactly one of config.model or llm"):
            PydanticAIAdapter(config, llm=llm)

    def test_create_agent_registers_content_null_history_processor(self):
        """The agent must sanitize content:null responses on every request.

        Registering the drop as a history-processing capability (not just the
        post-run storage filter) is what closes the mid-run gap: the model can emit
        an empty/thinking-only response within a single turn, and pydantic-ai would
        otherwise replay it to the provider as assistant content:null.
        """
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="test"))
        adapter.agent_name = "TestBot"

        with patch("band.adapters.pydantic_ai.Agent") as MockAgent:
            adapter._create_agent()
            history_processors = [
                capability.processor
                for capability in MockAgent.call_args.kwargs["capabilities"]
                if isinstance(capability, ProcessHistory)
            ]
            assert history_processors == [_drop_non_replayable_messages]

    def test_create_agent_registers_context_free_custom_tool(self):
        """A CustomToolDef-derived tool takes no RunContext, so it needs tool_plain.

        pydantic-ai 2.x rejects a context-free callable passed to ``agent.tool()``
        ("First parameter of tools that take context must be annotated with
        RunContext[...]"), which would break every tuple-form custom tool at agent
        creation. Built against a real Agent (TestModel, no network) because a
        patched Agent accepts either path and would prove nothing.
        """

        class Echo(BaseModel):
            """Echo the text back."""

            text: str

        async def handler(args: Echo) -> str:
            return args.text

        adapter = PydanticAIAdapter(llm=TestModel(), additional_tools=[(Echo, handler)])
        adapter.agent_name = "TestBot"

        agent = adapter._create_agent()

        registered = {
            name for toolset in agent.toolsets for name in getattr(toolset, "tools", ())
        }
        assert get_custom_tool_name(Echo) in registered

    def test_create_agent_registers_unannotated_context_custom_tool(self):
        """pydantic-ai injects an unannotated first parameter as context."""

        def echo(ctx, message: str) -> str:
            return message

        adapter = PydanticAIAdapter(llm=TestModel(), additional_tools=[echo])
        adapter.agent_name = "TestBot"

        agent = adapter._create_agent()
        echo_tool = next(
            tool
            for toolset in agent.toolsets
            for tool in getattr(toolset, "tools", {}).values()
            if tool.name == "echo"
        )

        assert echo_tool.function_schema.json_schema["required"] == ["message"]

    @pytest.mark.parametrize(
        "nothing_to_say",
        [
            pytest.param([], id="no-parts"),
            pytest.param([TextPart(content="")], id="blank-text"),
            pytest.param([ThinkingPart(content="done")], id="thinking-only"),
        ],
    )
    async def test_nothing_left_to_say_never_reruns_a_side_effecting_tool(
        self, nothing_to_say: list
    ):
        """One turn must post to the room exactly once, however the run ends.

        This agent answers through tools, so once it has acted it has nothing left to
        say — which providers spell in several ways. Each must end the run: forcing a
        satisfiable output instead spends an output retry per attempt, and every
        attempt sends the model a retry prompt asking it to return text *or call a
        tool*, which an agent told to answer only through tools obliges by re-posting
        the reply.

        The FunctionModel below stands in for that model: a tool call, then a
        nothing-to-say response, alternating — the shape a real run exhibits.
        """
        posted: list[str] = []

        class Note(BaseModel):
            """Post a note to the room."""

            text: str

        async def handler(args: Note) -> str:
            posted.append(args.text)
            return "posted"

        tool_name = get_custom_tool_name(Note)
        requests = 0

        def reply_via_tool_then_nothing(
            messages: list[ModelMessage], info: AgentInfo
        ) -> ModelResponse:
            nonlocal requests
            requests += 1
            if requests % 2:
                return ModelResponse(
                    parts=[ToolCallPart(tool_name=tool_name, args={"text": "hi"})]
                )
            return ModelResponse(parts=list(nothing_to_say))

        adapter = PydanticAIAdapter(
            llm=FunctionModel(reply_via_tool_then_nothing),
            additional_tools=[(Note, handler)],
        )
        adapter.agent_name = "TestBot"

        result = await adapter._create_agent().run("go", deps=FakeAgentTools())

        assert result.output is None
        assert posted == ["hi"]

    async def test_no_actionable_output_before_any_tool_ends_the_turn(self):
        """A model that says nothing must not blow the turn up before it acts.

        Thinking-mode models sometimes return no actionable output on the very first
        response, before any tool has run. Ending that run with no output lets the
        caller report a missing reply; without ``None`` as a valid outcome the
        refused output budget raises UnexpectedModelBehavior and fails the whole
        turn instead.
        """

        def think_only(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[ThinkingPart(content="hmm..."), TextPart("")])

        adapter = PydanticAIAdapter(llm=FunctionModel(think_only))
        adapter.agent_name = "TestBot"

        result = await adapter._create_agent().run("go", deps=MagicMock())

        assert result.output is None


__all__ = [
    "instrument_all_restored",
    "mock_pydantic_agent",
    "mock_tools",
    "sample_message",
    "trace_capture",
]
