"""PROOF SPIKE (Tier-1 model-output injection + dispatch/emission observation).

Throwaway-but-real proof that ONE framework-agnostic model-decision script can
drive adapters of two DIFFERENT seam types through their **real** routing path
and be observed at the right seam — with no live inference, no secrets.

    - LangGraph : INJECTABLE_OBJECT  -> fake BaseChatModel installed via ``llm=``
    - Anthropic : INTERNAL_CLIENT    -> substitute the declared ``_call_anthropic`` seam

It proves the four things the Tier-1 *dispatch + emission* contract must cover
(the request-construction reads — prompt/history/roster/capability-gating — are a
separate seam, owned by the Tier-1 platform stand-in, not this contract):

1. **Platform tool-call dispatch** (L0/L5): injected decision -> real routing ->
   right tool, right args, observed on the shared recorder's ``tool_calls``.
2. **Custom tool-call dispatch** (L1): custom tools dispatch through a DIFFERENT
   path (``execute_custom_tool`` / a native framework tool), so they are observed
   via the registered handler stub's own log — NOT the platform recorder. Proves
   the observation convention covers both dispatch paths.
3. **Execution-event emission + ordering** (L6): two sequential tool calls emit
   ``tool_call``/``tool_result`` events in invocation order, each result
   correlated to its call, with canonical type strings.
4. **Falsifiability**: a text-only decision dispatches nothing.

What is faked: only the model decision (one shim per family). What is real: the
adapter, its tool-exposure path, the framework's dispatch loop, the platform-tool
argument schema, and the event-emission path. A spike to validate the contract
before it is ratified — not the production harness.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, cast

import pytest
from pydantic import BaseModel, Field

from thenvoi.core.protocols import AgentToolsProtocol
from thenvoi.core.simple_adapter import SimpleAdapter
from thenvoi.core.types import AdapterFeatures, Emit, PlatformMessage
from thenvoi.testing.fake_tools import FakeAgentTools

pytest.importorskip("langchain", reason="langgraph extra not installed")
pytest.importorskip("anthropic", reason="anthropic extra not installed")


# ---------------------------------------------------------------------------
# The neutral, framework-agnostic model-decision representation.
#
# A scenario's Tier-1 input is a *script*: adapters run a tool loop, invoking the
# model repeatedly (call tool -> see result -> decide again), so a single
# decision is not enough. One ModelDecision is consumed per model invocation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]
    id: str | None = None


@dataclass(frozen=True)
class ModelDecision:
    """One model turn: optional text and/or an ordered set of tool calls."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


# A scenario expressed ONCE, neutrally: call send_message, then stop.
SEND_ARGS: dict[str, Any] = {
    "content": "Injected reply: PINEAPPLE",
    "mentions": ["@tester"],
}
SCRIPT: list[ModelDecision] = [
    ModelDecision(tool_calls=[ToolCall(name="thenvoi_send_message", args=SEND_ARGS)]),
    ModelDecision(text="done"),
]


class LogKeywordInput(BaseModel):
    """Append a keyword to a harness-readable log, then return a fixed token."""

    message: str = Field(..., description="Text to record in the harness log.")


@dataclass
class CustomToolProbe:
    """A registered custom tool whose handler is the signature-logging stub.

    ``log`` is where custom-tool dispatch is observed — deliberately NOT the
    platform recorder, because custom tools take a different dispatch path.
    """

    tool: Any  # framework-native custom tool (LangChain tool or CustomToolDef)
    log: list[str]
    name: str  # the tool name the model must emit for this framework


class _SchemaTools(FakeAgentTools):
    """Recorder that exposes canonical platform schemas to Anthropic."""

    def get_tool_schemas(
        self,
        format: str,
        *,
        include_memory: bool = False,
        include_contacts: bool = True,
    ) -> list[dict[str, Any]]:
        if format != "anthropic":
            return super().get_tool_schemas(
                format,
                include_memory=include_memory,
                include_contacts=include_contacts,
            )

        from thenvoi.runtime.tools import iter_tool_definitions

        schemas: list[dict[str, Any]] = []
        for definition in iter_tool_definitions(
            include_memory=include_memory,
            include_contacts=include_contacts,
        ):
            input_schema = definition.input_model.model_json_schema()
            input_schema.pop("title", None)
            schemas.append(
                {
                    "name": definition.name,
                    "description": definition.input_model.__doc__ or "",
                    "input_schema": input_schema,
                }
            )
        return schemas

    def get_anthropic_tool_schemas(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.get_tool_schemas("anthropic", **kwargs)


# ---------------------------------------------------------------------------
# Per-family translators + injection bindings.
#
# Each binding declares ONE seam and supplies the translator that renders the
# neutral ModelDecision into that framework's native model output, installed at
# the seam. This is the per-adapter shim the contract calls for.
# ---------------------------------------------------------------------------


def _make_langgraph_adapter(
    script: list[ModelDecision],
    *,
    features: AdapterFeatures | None = None,
    additional_tools: list[Any] | None = None,
) -> SimpleAdapter[Any]:
    """INJECTABLE_OBJECT seam: a fake BaseChatModel passed via ``llm=``.

    The fake replays the script as AIMessages. LangGraph's real create_agent
    graph (tool binding, tool-call parsing, dispatch loop) runs on top of it.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langgraph.checkpoint.memory import InMemorySaver

    from thenvoi.adapters.langgraph import LangGraphAdapter

    class _ScriptedChatModel(BaseChatModel):
        # Declared as a pydantic field; mutated in place (popped) per call.
        decisions: list[Any]

        @property
        def _llm_type(self) -> str:
            return "scripted-injection-fake"

        def _next(self) -> AIMessage:
            decision = self.decisions.pop(0)
            if decision.tool_calls:
                return AIMessage(
                    content=decision.text or "",
                    tool_calls=[
                        {
                            "name": tc.name,
                            "args": tc.args,
                            "id": tc.id or f"call_{i}",
                            "type": "tool_call",
                        }
                        for i, tc in enumerate(decision.tool_calls)
                    ],
                )
            return AIMessage(content=decision.text or "")

        def _generate(
            self,
            messages: list[Any],
            stop: Any = None,
            run_manager: Any = None,
            **kw: Any,
        ) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        async def _agenerate(
            self,
            messages: list[Any],
            stop: Any = None,
            run_manager: Any = None,
            **kw: Any,
        ) -> ChatResult:
            return ChatResult(generations=[ChatGeneration(message=self._next())])

        # create_agent calls bind_tools; the real tools are still exposed and
        # dispatched by the graph — we just don't need them to script a decision.
        def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
            return self

    return LangGraphAdapter(
        llm=_ScriptedChatModel(decisions=list(script)),
        checkpointer=InMemorySaver(),
        additional_tools=additional_tools,
        features=features,
    )


def _langgraph_custom_tool() -> CustomToolProbe:
    """A LangChain StructuredTool whose coroutine records its invocation."""
    from langchain_core.tools import StructuredTool

    log: list[str] = []

    async def _log_keyword(message: str) -> str:
        log.append(message)
        return "FLIBBERTIGIBBET"

    tool = StructuredTool.from_function(
        coroutine=_log_keyword,
        name="log_keyword",
        description="Append a keyword to a harness log and return a token.",
        args_schema=LogKeywordInput,
    )
    return CustomToolProbe(tool=tool, log=log, name="log_keyword")


def _make_anthropic_adapter(
    script: list[ModelDecision],
    *,
    features: AdapterFeatures | None = None,
    additional_tools: list[Any] | None = None,
) -> SimpleAdapter[Any]:
    """INTERNAL_CLIENT seam: substitute the declared ``_call_anthropic`` method.

    The fake returns native Anthropic Message shapes (real ToolUseBlock /
    TextBlock). The adapter's own tool loop (_process_tool_calls) runs on top
    and dispatches through ``tools.execute_tool_call`` (platform) or
    ``execute_custom_tool`` (custom).
    """
    from anthropic.types import TextBlock, ToolUseBlock

    from thenvoi.adapters.anthropic import AnthropicAdapter

    adapter = AnthropicAdapter(
        model="claude-sonnet-4-5-20250929",
        additional_tools=additional_tools,
        features=features,
    )

    cursor = list(script)

    async def _scripted_call_anthropic(messages: Any, tools: Any) -> Any:
        decision = cursor.pop(0)
        exposed_names = {t.get("name") for t in tools}
        missing = [
            tc.name for tc in decision.tool_calls if tc.name not in exposed_names
        ]
        assert not missing, (
            "Anthropic spike must exercise the adapter's tool-exposure leg; "
            f"missing scripted tools from schema: {missing!r}"
        )
        if decision.tool_calls:
            content = [
                ToolUseBlock(
                    id=tc.id or f"toolu_{i}",
                    name=tc.name,
                    input=tc.args,
                    type="tool_use",
                )
                for i, tc in enumerate(decision.tool_calls)
            ]
            return _Resp(stop_reason="tool_use", content=content)
        return _Resp(
            stop_reason="end_turn",
            content=[TextBlock(text=decision.text or "done", type="text")],
        )

    # Substitute the declared seam on the instance (the contract's "declared
    # seam" install; production signatures are untouched).
    adapter._call_anthropic = _scripted_call_anthropic  # type: ignore[method-assign]
    return adapter


def _anthropic_custom_tool() -> CustomToolProbe:
    """An (InputModel, handler) CustomToolDef whose handler records its call."""
    from thenvoi.runtime.custom_tools import get_custom_tool_name

    log: list[str] = []

    async def _handler(args: LogKeywordInput) -> str:
        log.append(args.message)
        return "FLIBBERTIGIBBET"

    return CustomToolProbe(
        tool=(LogKeywordInput, _handler),
        log=log,
        name=get_custom_tool_name(LogKeywordInput),  # "logkeyword"
    )


@dataclass(frozen=True)
class _Resp:
    """Minimal stand-in for an anthropic Message (only what the adapter reads)."""

    stop_reason: str
    content: list[Any]


@dataclass(frozen=True)
class InjectionBinding:
    framework_id: str
    seam_type: str
    make_adapter: Callable[..., SimpleAdapter[Any]]
    make_custom_tool: Callable[[], CustomToolProbe]


BINDINGS: list[InjectionBinding] = [
    InjectionBinding(
        "langgraph",
        "INJECTABLE_OBJECT",
        _make_langgraph_adapter,
        _langgraph_custom_tool,
    ),
    InjectionBinding(
        "anthropic", "INTERNAL_CLIENT", _make_anthropic_adapter, _anthropic_custom_tool
    ),
]


# ---------------------------------------------------------------------------
# Uniform driver + assertions (shared across all seam types).
# ---------------------------------------------------------------------------


def _make_msg(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="msg-1",
        room_id=room_id,
        content="Say the magic word",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


async def _run(
    adapter: SimpleAdapter[Any], tools: FakeAgentTools, room_id: str
) -> None:
    await adapter.on_started("ProofBot", "A bot under the Tier-1 injection proof.")
    await adapter.on_message(
        msg=_make_msg(room_id),
        tools=cast("AgentToolsProtocol", tools),
        history=[],
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id=room_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.framework_id)
async def test_platform_tool_dispatch_right_tool_right_args(
    binding: InjectionBinding,
) -> None:
    """L0/L5: one neutral script -> real routing -> right tool, right args."""
    room_id = "room-injection-proof"
    tools = _SchemaTools(room_id=room_id)  # the shared recorder
    await _run(binding.make_adapter(SCRIPT), tools, room_id)

    dispatched = [
        c for c in tools.tool_calls if c["tool_name"] == "thenvoi_send_message"
    ]
    assert len(dispatched) == 1, (
        f"[{binding.framework_id}/{binding.seam_type}] expected exactly one "
        f"thenvoi_send_message dispatch, got: {tools.tool_calls}"
    )
    assert dispatched[0]["arguments"] == SEND_ARGS, (
        f"[{binding.framework_id}/{binding.seam_type}] wrong args at dispatch: "
        f"{dispatched[0]['arguments']!r} != {SEND_ARGS!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.framework_id)
async def test_custom_tool_dispatch_observed_via_handler_not_recorder(
    binding: InjectionBinding,
) -> None:
    """L1.3: custom-tool dispatch takes a different path than platform tools.

    It is observed via the registered handler stub's own log, and is NOT seen by
    the platform recorder — proving the observation convention must cover BOTH
    dispatch paths.
    """
    room_id = "room-custom-tool"
    probe = binding.make_custom_tool()
    script = [
        ModelDecision(
            tool_calls=[ToolCall(name=probe.name, args={"message": "M1_PROBE"})]
        ),
        ModelDecision(text="done"),
    ]
    tools = _SchemaTools(room_id=room_id)
    await _run(
        binding.make_adapter(script, additional_tools=[probe.tool]), tools, room_id
    )

    # Observed via the custom handler stub (its own log) — the real dispatch path.
    assert probe.log == ["M1_PROBE"], (
        f"[{binding.framework_id}/{binding.seam_type}] custom tool handler did not "
        f"fire with the injected args; log={probe.log!r}"
    )
    # The platform recorder did NOT see it — custom tools bypass execute_tool_call.
    assert tools.tool_calls == [], (
        f"[{binding.framework_id}/{binding.seam_type}] custom-tool dispatch leaked "
        f"into the platform recorder: {tools.tool_calls}"
    )


# Two distinct platform tools, supplied across two turns so invocation order is
# deterministic for every framework. Explicit ids keep correlation unambiguous.
ORDER_SCRIPT: list[ModelDecision] = [
    ModelDecision(
        tool_calls=[
            ToolCall("thenvoi_add_participant", {"identifier": "@echo"}, id="call_A")
        ]
    ),
    ModelDecision(tool_calls=[ToolCall("thenvoi_get_participants", {}, id="call_B")]),
    ModelDecision(text="done"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.framework_id)
async def test_execution_events_emitted_in_order_and_correlated(
    binding: InjectionBinding,
) -> None:
    """L6.2-4/6: sequential tool calls emit paired, ordered, correlated events.

    With execution emission enabled, tool A then tool B must produce
    tool_call/tool_result events in invocation order, each result correlated to
    its originating call, with canonical type strings and non-empty payloads.
    """
    room_id = "room-emission"
    tools = _SchemaTools(room_id=room_id)
    adapter = binding.make_adapter(
        ORDER_SCRIPT, features=AdapterFeatures(emit={Emit.EXECUTION})
    )
    await _run(adapter, tools, room_id)

    events = tools.events_sent

    # Canonical type strings, in invocation order, call paired with result (L6.6/3/4).
    types = [e["message_type"] for e in events]
    assert types == ["tool_call", "tool_result", "tool_call", "tool_result"], (
        f"[{binding.framework_id}/{binding.seam_type}] event types/order wrong: {types}"
    )

    payloads = [json.loads(e["content"]) for e in events]
    assert all(e["content"] for e in events), "empty event payload emitted"

    # Tool A's call/result precede tool B's call/result (L6.4 — invocation order).
    call_names = [
        p["name"] for e, p in zip(events, payloads) if e["message_type"] == "tool_call"
    ]
    assert call_names == ["thenvoi_add_participant", "thenvoi_get_participants"], (
        f"[{binding.framework_id}/{binding.seam_type}] calls out of order: {call_names}"
    )

    # Each result correlates to its call; the two call ids are distinct (L6.3/5).
    assert payloads[0]["tool_call_id"] == payloads[1]["tool_call_id"]
    assert payloads[2]["tool_call_id"] == payloads[3]["tool_call_id"]
    assert payloads[0]["tool_call_id"] != payloads[2]["tool_call_id"], (
        f"[{binding.framework_id}/{binding.seam_type}] tool_call ids not distinct"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", BINDINGS, ids=lambda b: b.framework_id)
async def test_negative_control_text_only_does_not_dispatch(
    binding: InjectionBinding,
) -> None:
    """Falsifiability: a text-only decision must produce NO tool dispatch.

    Proves the recorder logs *real* dispatch off the adapter's routing path
    rather than a constant — so the positive tests are not vacuous.
    """
    room_id = "room-injection-negative"
    tools = _SchemaTools(room_id=room_id)
    await _run(
        binding.make_adapter([ModelDecision(text="just a reply, no tools")]),
        tools,
        room_id,
    )

    assert tools.tool_calls == [], (
        f"[{binding.framework_id}/{binding.seam_type}] expected no dispatch for a "
        f"text-only decision, got: {tools.tool_calls}"
    )
