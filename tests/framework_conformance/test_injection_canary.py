"""Positive-routing canary gate for the Tier-1 ``InjectionBinding`` registry.

This is the "seam exists but routing went stale" alarm the contract calls for
(§5.6). For **every honest binding**, it drives one fixed canary decision —
``band_send_message(content="CANARY", mentions=["@canary"])`` — through the
adapter's *real* routing at the binding's *declared* seam, then asserts the
dispatch lands on the binding's *declared* ``observation_paths`` bucket of the
shared recorder.

Why this is more than the per-adapter spikes: it is **fail-closed and registry-
driven**. An honest binding with no registered canary builder fails
``test_every_honest_binding_has_a_canary_builder`` — so a newly-honest adapter
cannot be added to the registry without a live routing proof. And because every
builder asserts through one shared driver, a seam that still *resolves* (so the
drift gate passes) but no longer *routes a call to the recorder* fails here.

The builders deliberately reuse the proven seam patterns from the per-adapter
spikes; this module is the generalisation, not a replacement.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from typing import Any, cast

import pytest

from tests.framework_conformance.injection_registry import (
    INJECTION_BINDINGS,
    InjectionBinding,
    ObservationPath,
    tier1_dependency_blocked_reason,
)
from band.core.protocols import AgentToolsProtocol
from band.core.types import PlatformMessage
from band.testing.fake_tools import FakeAgentTools

# The one fixed canary decision every honest binding must route.
_CANARY_TOOL = "band_send_message"
_CANARY_ARGS: dict[str, Any] = {"content": "CANARY", "mentions": ["@canary"]}
_SENTINEL_OPENAI_API_KEY = "sk-tier1-conformance-sentinel-not-a-secret"
_PROVIDER_BASE_URL_ENV_VARS = (
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_API_HOST",
)


@contextmanager
def _tier1_sentinel_provider_env() -> Generator[None, None, None]:
    names = ("OPENAI_API_KEY", *_PROVIDER_BASE_URL_ENV_VARS)
    original = {name: os.environ.get(name) for name in names}
    try:
        os.environ["OPENAI_API_KEY"] = _SENTINEL_OPENAI_API_KEY
        for name in _PROVIDER_BASE_URL_ENV_VARS:
            os.environ.pop(name, None)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# Every honest adapter is told the same send_message schema, so each framework's
# real tool-exposure path has a valid declaration to bind.
def _send_message_schema(format: str) -> dict[str, Any]:
    from band.runtime.tools import TOOL_DEFINITIONS

    definition = TOOL_DEFINITIONS[_CANARY_TOOL]
    input_schema = definition.input_model.model_json_schema()
    input_schema.pop("title", None)

    if format == "openai":
        return {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.input_model.__doc__ or "",
                "parameters": input_schema,
            },
        }
    if format == "anthropic":
        return {
            "name": definition.name,
            "description": definition.input_model.__doc__ or "",
            "input_schema": input_schema,
        }
    raise ValueError(f"Unsupported schema format: {format}")


class _SchemaTools(FakeAgentTools):
    def get_openai_tool_schemas(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [_send_message_schema("openai")]

    def get_anthropic_tool_schemas(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [_send_message_schema("anthropic")]


def _canary_msg(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="canary-msg-1",
        room_id=room_id,
        content="route the canary",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


async def _drive(adapter: Any, tools: FakeAgentTools, room_id: str) -> None:
    await adapter.on_started("CanaryBot", "Tier-1 positive-routing canary bot.")
    await adapter.on_message(
        msg=_canary_msg(room_id),
        tools=cast("AgentToolsProtocol", tools),
        history=_history_for(adapter),
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=room_id,
    )


def _history_for(adapter: Any) -> Any:
    """Most adapters accept an empty list; Codex needs a CodexSessionState."""
    if type(adapter).__name__ == "CodexAdapter":
        from band.integrations.codex.types import CodexSessionState

        return CodexSessionState(room_id="canary")
    return []


# ---------------------------------------------------------------------------
# Per-adapter canary builders. Each installs the canary decision at the
# binding's declared seam via the REAL adapter, then runs it.
# A builder returns the FakeAgentTools recorder after the drive completes.
# ---------------------------------------------------------------------------


@dataclass
class _CanaryResult:
    tools: FakeAgentTools


async def _canary_langgraph() -> _CanaryResult:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver

    from band.adapters.langgraph import LangGraphAdapter

    decisions = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": _CANARY_TOOL,
                    "args": _CANARY_ARGS,
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    class _ScriptedChatModel(BaseChatModel):
        remaining: list[Any]

        @property
        def _llm_type(self) -> str:
            return "canary-fake"

        def _next(self) -> AIMessage:
            return self.remaining.pop(0)

        def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        async def _agenerate(
            self, messages, stop=None, run_manager=None, **kw
        ) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

    adapter = LangGraphAdapter(
        llm=_ScriptedChatModel(remaining=list(decisions)),
        checkpointer=InMemorySaver(),
    )
    tools = _SchemaTools(room_id="canary-langgraph")
    await _drive(adapter, tools, "canary-langgraph")
    return _CanaryResult(tools)


async def _canary_anthropic() -> _CanaryResult:
    from anthropic.types import TextBlock, ToolUseBlock

    from band.adapters.anthropic import AnthropicAdapter

    @dataclass(frozen=True)
    class _Resp:
        stop_reason: str
        content: list[Any]

    adapter = AnthropicAdapter(model="claude-sonnet-4-5-20250929")
    cursor = [
        _Resp(
            "tool_use",
            [
                ToolUseBlock(
                    id="t1", name=_CANARY_TOOL, input=_CANARY_ARGS, type="tool_use"
                )
            ],
        ),
        _Resp("end_turn", [TextBlock(text="done", type="text")]),
    ]

    async def _scripted(messages: Any, tools: Any) -> Any:
        return cursor.pop(0)

    adapter._call_anthropic = _scripted  # type: ignore[method-assign]
    tools = _SchemaTools(room_id="canary-anthropic")
    await _drive(adapter, tools, "canary-anthropic")
    return _CanaryResult(tools)


async def _canary_gemini() -> _CanaryResult:
    from google.genai import types

    from band.adapters.gemini import GeminiAdapter

    def _resp_tool() -> types.GenerateContentResponse:
        part = types.Part(
            function_call=types.FunctionCall(
                name=_CANARY_TOOL, args=_CANARY_ARGS, id="g1"
            )
        )
        content = types.Content(role="model", parts=[part])
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=content)]
        )

    def _resp_text() -> types.GenerateContentResponse:
        content = types.Content(role="model", parts=[types.Part(text="done")])
        return types.GenerateContentResponse(
            candidates=[types.Candidate(content=content)]
        )

    adapter = GeminiAdapter(model="gemini-2.5-flash", provider_key="unused-in-canary")
    cursor = [_resp_tool(), _resp_text()]

    async def _scripted(contents: Any, tools: Any) -> Any:
        return cursor.pop(0)

    adapter._call_gemini = _scripted  # type: ignore[method-assign]
    tools = _SchemaTools(room_id="canary-gemini")
    await _drive(adapter, tools, "canary-gemini")
    return _CanaryResult(tools)


async def _canary_pydantic_ai() -> _CanaryResult:
    import json

    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    from band.adapters.pydantic_ai import PydanticAIAdapter

    turns: list[Any] = [
        ("tool", _CANARY_TOOL, json.dumps(_CANARY_ARGS)),
        ("text", "done"),
    ]

    async def _stream(messages: Any, info: AgentInfo) -> Any:
        decision = turns.pop(0) if turns else ("text", "done")
        if decision[0] == "tool":
            _, name, json_args = decision
            yield {0: DeltaToolCall(name=name, json_args=json_args, tool_call_id="c1")}
        else:
            yield decision[1]

    tools = FakeAgentTools(room_id="canary-pyai")
    with _tier1_sentinel_provider_env():
        adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")
        await adapter.on_started("CanaryBot", "Tier-1 positive-routing canary bot.")
        assert adapter._agent is not None
        with adapter._agent.override(model=FunctionModel(stream_function=_stream)):
            await adapter.on_message(
                msg=_canary_msg("canary-pyai"),
                tools=cast("AgentToolsProtocol", tools),
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="canary-pyai",
            )
    return _CanaryResult(tools)


async def _canary_google_adk() -> _CanaryResult:
    from google.adk import Agent as ADKAgent
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from band.adapters.google_adk import GoogleADKAdapter, _sanitize_adk_agent_name

    class _ScriptedBaseLlm(BaseLlm):
        script: list[Any]

        @property
        def _llm_type(self) -> str:
            return "canary-fake"

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> Any:
            decision = self.script.pop(0) if self.script else None
            if decision is None:
                yield LlmResponse(
                    content=types.Content(role="model", parts=[types.Part(text="done")])
                )
                return
            name, args = decision
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(name=name, args=args)
                        )
                    ],
                )
            )

    adapter = GoogleADKAdapter(model="gemini-2.5-flash")

    def _scripted_create_runner(tools: AgentToolsProtocol) -> InMemoryRunner:
        adk_tools = adapter._build_adk_tools(tools)
        adk_agent = ADKAgent(
            name=_sanitize_adk_agent_name(adapter.agent_name),
            model=_ScriptedBaseLlm(
                model="scripted", script=[(_CANARY_TOOL, _CANARY_ARGS), None]
            ),
            instruction=adapter._system_prompt,
            tools=adk_tools,
        )
        return InMemoryRunner(agent=adk_agent, app_name="band")

    adapter._create_runner = _scripted_create_runner  # type: ignore[method-assign]
    tools = _SchemaTools(room_id="canary-adk")
    await _drive(adapter, tools, "canary-adk")
    return _CanaryResult(tools)


async def _canary_codex() -> _CanaryResult:
    # Reuse the real-wire replay machinery from the Codex spike, but script the
    # canary args into the captured item/tool/call frame so the canary asserts
    # the same content as every other adapter.
    from tests.framework_conformance.codex_replay import (
        ReplayCodexClient,
        frames_with_tool_call,
    )
    from band.adapters.codex import CodexAdapter, CodexAdapterConfig

    canary_frames = frames_with_tool_call(_CANARY_TOOL, _CANARY_ARGS)

    adapter = CodexAdapter(
        config=CodexAdapterConfig(transport="stdio", model="gpt-5.4"),
        client_factory=lambda _config: ReplayCodexClient(canary_frames),
    )
    tools = _SchemaTools(room_id="canary-codex")
    await _drive(adapter, tools, "canary-codex")
    return _CanaryResult(tools)


_CANARY_BUILDERS: dict[str, Callable[[], Any]] = {
    "langgraph": _canary_langgraph,
    "anthropic": _canary_anthropic,
    "gemini": _canary_gemini,
    "pydantic_ai": _canary_pydantic_ai,
    "google_adk": _canary_google_adk,
    "codex": _canary_codex,
}


def _honest_bindings() -> list[InjectionBinding]:
    return [b for b in INJECTION_BINDINGS if b.is_honest()]


def _observed_dispatch_count(binding: InjectionBinding, tools: FakeAgentTools) -> int:
    """Count canary dispatches that carry the EXACT canary args, on the binding's
    declared observation buckets.

    Matching the full args (not just the tool name / content) is what lets the
    canary catch a route that dispatches the right tool with corrupted args —
    e.g. a translator that drops ``mentions``. A name-only match would pass such
    a regression.
    """
    total = 0
    if ObservationPath.EXECUTE_TOOL_CALL in binding.observation_paths:
        total += len(
            [
                c
                for c in tools.tool_calls
                if c["tool_name"] == _CANARY_TOOL and c["arguments"] == _CANARY_ARGS
            ]
        )
    if ObservationPath.TYPED_METHODS in binding.observation_paths:
        # band_send_message routes to the typed send_message(content, mentions).
        total += len(
            [
                m
                for m in tools.messages_sent
                if m["content"] == _CANARY_ARGS["content"]
                and m["mentions"] == _CANARY_ARGS["mentions"]
            ]
        )
    return total


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------


def test_every_honest_binding_has_a_canary_builder() -> None:
    """Fail-closed: a newly-honest adapter cannot be added without a routing proof."""
    missing = [
        b.adapter for b in _honest_bindings() if b.adapter not in _CANARY_BUILDERS
    ]
    assert not missing, (
        f"Honest InjectionBindings without a positive-routing canary builder: {missing}. "
        f"Add a builder in tests/framework_conformance/test_injection_canary.py."
    )


def test_no_orphan_canary_builders() -> None:
    """Every builder maps to an honest binding (no stale builders)."""
    honest_ids = {b.adapter for b in _honest_bindings()}
    orphans = set(_CANARY_BUILDERS) - honest_ids
    assert not orphans, f"Canary builders with no matching honest binding: {orphans}."


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", _honest_bindings(), ids=lambda b: b.adapter)
async def test_canary_routes_to_declared_observation_path(
    binding: InjectionBinding,
) -> None:
    """The canary decision routes through real adapter dispatch to the declared bucket."""
    builder = _CANARY_BUILDERS.get(binding.adapter)
    if builder is None:
        pytest.fail(
            f"{binding.adapter}: no canary builder (see fail-closed gate above)"
        )

    blocked_reason = tier1_dependency_blocked_reason(binding)
    if blocked_reason is not None:
        pytest.fail(blocked_reason)

    try:
        result = await builder()
    except ImportError as exc:
        pytest.fail(
            f"tier1_dependency_blocked: {binding.adapter} import failed after "
            f"declared dependencies resolved: {exc}"
        )

    count = _observed_dispatch_count(binding, result.tools)
    assert count == 1, (
        f"{binding.adapter}: canary must dispatch exactly once on declared "
        f"observation_paths {sorted(p.value for p in binding.observation_paths)}. "
        f"tool_calls={result.tools.tool_calls}, messages_sent={result.tools.messages_sent}"
    )
