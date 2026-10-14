"""Instrumentation behavior for Pydantic AI."""

from __future__ import annotations

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from band.adapters.pydantic_ai import PydanticAIAdapter
from tests.adapters.pydanticai.kit import TraceCapture, _reply


class TestInstrumentation:
    """Tests for the ``instrument`` pass-through to pydantic-ai.

    Band creates no TracerProvider and no exporter: the host owns the pipeline and
    hands the agent an ``InstrumentationSettings`` (or flips ``Agent.instrument_all``).
    """

    def test_settings_route_the_run_into_the_host_tracer(
        self, trace_capture: TraceCapture, mock_tools
    ):
        """Explicit settings trace the model call and the agent run."""
        adapter = PydanticAIAdapter(llm=_reply("ok"), instrument=trace_capture.settings)
        adapter.agent_name = "TestBot"

        adapter._create_agent().run_sync("hello", deps=mock_tools)

        assert trace_capture.operations() == ["chat", "invoke_agent"]

    def test_true_traces_through_the_ambient_provider(
        self,
        trace_capture: TraceCapture,
        monkeypatch: pytest.MonkeyPatch,
        mock_tools,
    ):
        """``True`` is the shorthand for a host that published its providers.

        pydantic-ai resolves the ambient ``TracerProvider`` when it builds the
        default settings, so this only traces in a process that set one — which
        is why examples/opentelemetry hands over settings instead.
        """
        monkeypatch.setattr(
            "pydantic_ai.models.instrumented.get_tracer_provider",
            lambda: trace_capture.provider,
        )
        adapter = PydanticAIAdapter(llm=_reply("ok"), instrument=True)
        adapter.agent_name = "TestBot"

        adapter._create_agent().run_sync("hello", deps=mock_tools)

        assert trace_capture.operations() == ["chat", "invoke_agent"]

    def test_default_inherits_instrument_all(
        self, trace_capture: TraceCapture, instrument_all_restored, mock_tools
    ):
        """Passing nothing leaves the host's process-wide choice in force."""
        Agent.instrument_all(trace_capture.settings)
        adapter = PydanticAIAdapter(llm=_reply("ok"))
        adapter.agent_name = "TestBot"

        adapter._create_agent().run_sync("hello", deps=mock_tools)

        assert trace_capture.operations() == ["chat", "invoke_agent"]

    def test_false_opts_out_of_instrument_all(
        self, trace_capture: TraceCapture, instrument_all_restored, mock_tools
    ):
        """``False`` is not "unset": it excludes this agent from a traced process."""
        Agent.instrument_all(trace_capture.settings)
        adapter = PydanticAIAdapter(llm=_reply("ok"), instrument=False)
        adapter.agent_name = "TestBot"

        adapter._create_agent().run_sync("hello", deps=mock_tools)

        assert trace_capture.operations() == []

    def test_instrumented_agent_still_drops_content_null_history(
        self, trace_capture: TraceCapture, mock_tools
    ):
        """Instrumentation must not cost the ProcessHistory capability.

        Both ride on the agent, and an implementation that passed instrumentation
        as a capability would silently replace the history processor — sending
        providers the thinking-only response as assistant ``content: null``.
        """
        seen: list[list[ModelMessage]] = []

        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(list(messages))
            return ModelResponse(parts=[TextPart(content="ok")])

        adapter = PydanticAIAdapter(
            llm=FunctionModel(respond), instrument=trace_capture.settings
        )
        adapter.agent_name = "TestBot"

        adapter._create_agent().run_sync(
            "hello",
            deps=mock_tools,
            message_history=[
                ModelRequest(parts=[UserPromptPart(content="earlier")]),
                ModelResponse(parts=[ThinkingPart(content="hmm")]),
            ],
        )

        (sent_to_model,) = seen
        assert not [m for m in sent_to_model if isinstance(m, ModelResponse)]
        assert trace_capture.operations() == ["chat", "invoke_agent"]
