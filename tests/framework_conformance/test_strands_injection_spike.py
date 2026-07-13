"""Tier-1 conformance spike for the Strands adapter (INJECTABLE_MODEL_OBJECT family).

WHAT THIS PROVES
----------------
Given a scripted model decision, the StrandsAdapter's real tool wrappers (built
by ``_build_platform_tools`` and run by Strands' own agent loop) dispatch the
tool to the platform via ``AgentToolsProtocol`` — no live inference, no secrets,
no provider API key.

WHY IT IS HONEST (not circular)
-------------------------------
The faked decision is installed at the **public** ``Agent(model=...)`` seam:
``strands.models.Model`` is the framework's documented provider ABC, and the
adapter passes its ``model`` constructor argument straight through in
``_build_agent``. Strands ships no dedicated test model, so the spike implements
the minimal ``Model`` subclass; the scripted surface is still the stable public
provider contract (``model_seam_kind=PUBLIC_TEST_MODEL``), though the
``StreamEvent`` dict shapes it emits move with the framework's fast release
cadence (``drift_risk=HIGH``, pinned ``strands-agents>=1.40,<2``).

OBSERVATION PATH (the load-bearing detail)
------------------------------------------
Strands platform-tool wrappers call **typed** ``AgentToolsProtocol`` methods
directly — ``band_send_message`` calls ``tools.send_message(...)`` — NOT
``execute_tool_call``. So dispatch is observed on ``FakeAgentTools.messages_sent``
(``observation_paths={TYPED_METHODS}``); a canary that only watched
``tool_calls`` would wrongly fail Strands.

STEP-0 FINDINGS (verified against installed strands-agents 1.47.0)
------------------------------------------------------------------
1. Per-invocation context: ``agent.invoke_async(prompt, invocation_state={...})``
   reaches tool bodies via ``@tool(context=True)`` (``tool_context.invocation_state``)
   and every hook event (``event.invocation_state``). No fallback needed.
2. Scripted model events (consumed by ``strands.event_loop.streaming``): one tool
   call is ``messageStart{role:assistant}`` ->
   ``contentBlockStart{start:{toolUse:{toolUseId,name}}}`` ->
   ``contentBlockDelta{delta:{toolUse:{input:<json-string>}}}`` ->
   ``contentBlockStop`` -> ``messageStop{stopReason:"tool_use"}``; a terminal
   text turn uses ``delta:{text}`` + ``stopReason:"end_turn"``; per-call usage
   rides an optional trailing ``metadata{usage}`` event. The ``Model`` ABC also
   requires ``structured_output`` (abstract), implemented as a stub here.
3. OpenAI provider: ``strands.models.openai.OpenAIModel(client_args={"api_key":...},
   model_id=...)``. There is NO provider-prefix string shorthand — a bare string
   to ``Agent(model=...)`` is treated as a *Bedrock* model id.
4. Hook events: ``BeforeToolCallEvent.tool_use{name,toolUseId,input}``,
   ``AfterToolCallEvent.result: ToolResult{status,content,toolUseId}``; async
   callbacks are supported. Usage lives on ``AgentResult.metrics.accumulated_usage``
   (``inputTokens``/``outputTokens``/``cacheReadInputTokens``/``cacheWriteInputTokens``)
   and accumulates across turns on a shared Agent — the adapter constructs a
   per-turn Agent, so the accumulated value is exactly the turn's usage.
5. History handle: ``agent.messages`` is a public read/write list of Converse
   ``Message`` dicts; pre-seeding via ``Agent(messages=...)`` and reading back
   after a run both work, so Band owns per-room history.

InjectionBinding for the Tier-1 injection registry (lands with the taxonomy
branch; recorded here until ``injection_registry.py`` exists on this branch):

    InjectionBinding(
        adapter="strands",
        family=Family.INJECTABLE_MODEL_OBJECT,
        tier1_status=Tier1Status.HONEST_TODAY,
        drift_risk=DriftRisk.HIGH,
        observation_paths=frozenset({ObservationPath.TYPED_METHODS}),
        seam="band.adapters.strands:StrandsAdapter._build_agent",
        model_seam_kind=ModelSeamKind.PUBLIC_TEST_MODEL,
        spike_test="tests/framework_conformance/test_strands_injection_spike.py",
        version_pin="strands-agents>=1.40,<2",
        required_modules=("strands",),
        required_extra="dev",
    )
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, cast

import pytest
from pydantic import BaseModel

pytest.importorskip("strands", reason="strands extra not installed")

from strands.models import Model  # noqa: E402
from strands.types.content import Messages  # noqa: E402
from strands.types.streaming import StreamEvent  # noqa: E402
from strands.types.tools import ToolSpec  # noqa: E402

from band.adapters.strands import StrandsAdapter  # noqa: E402
from band.core.protocols import AgentToolsProtocol  # noqa: E402
from band.core.types import AdapterFeatures, Emit, PlatformMessage  # noqa: E402
from band.testing.fake_tools import FakeAgentTools  # noqa: E402

_SEND_CONTENT = "Injected reply: PINEAPPLE"
_SEND_MENTIONS = ["@tester"]


def _make_msg(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="strands-spike-1",
        room_id=room_id,
        content="Say the magic word",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


class _ScriptedStrandsModel(Model):
    """A streaming Model that replays one scripted decision per invocation.

    Each ``stream()`` call pops one decision and yields the Converse
    ``StreamEvent`` sequence Strands' event loop parses (see module docstring):

    * ``("tool", name, args_dict)`` -> a tool-use turn the agent dispatches;
    * ``("text", body)`` -> a text turn that ends the run.
    """

    def __init__(self, turns: list[Any]):
        self._turns = list(turns)
        self._config: dict[str, Any] = {}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    async def structured_output(
        self,
        output_model: Any,
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("spike model does not do structured output")
        yield {}  # pragma: no cover - makes this an async generator

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        decision = self._turns.pop(0) if self._turns else ("text", "done")
        yield {"messageStart": {"role": "assistant"}}
        if decision[0] == "tool":
            _, name, args = decision
            yield {
                "contentBlockStart": {
                    "start": {"toolUse": {"toolUseId": f"call-{name}", "name": name}}
                }
            }
            yield {
                "contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}
            }
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": decision[1]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


async def _run(adapter: StrandsAdapter, tools: FakeAgentTools, room_id: str) -> None:
    """Drive the adapter through its real lifecycle: on_started -> on_message."""
    await adapter.on_started("StrandsSpikeBot", "Tier-1 Strands injection spike bot.")
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
async def test_scripted_model_routes_to_typed_send_message() -> None:
    """A scripted tool-use decision drives the real platform-tool wrapper (L0).

    Observed on messages_sent (typed-method dispatch), NOT tool_calls.
    """
    room_id = "strands-spike-room"
    tools = FakeAgentTools(room_id=room_id)
    adapter = StrandsAdapter(
        model=_ScriptedStrandsModel(
            [
                (
                    "tool",
                    "band_send_message",
                    {"content": _SEND_CONTENT, "mentions": _SEND_MENTIONS},
                ),
                ("text", "done"),
            ]
        )
    )
    await _run(adapter, tools, room_id)

    # Strands dispatches platform tools through typed AgentToolsProtocol
    # methods, so observe on messages_sent (the contract's observation_paths).
    assert len(tools.messages_sent) == 1, (
        f"expected one send_message dispatch via the real tool wrapper, "
        f"got messages_sent={tools.messages_sent}, tool_calls={tools.tool_calls}"
    )
    assert tools.messages_sent[0]["content"] == _SEND_CONTENT
    assert tools.messages_sent[0]["mentions"] == _SEND_MENTIONS
    # And confirm it did NOT route through execute_tool_call (the dual-path proof).
    assert tools.tool_calls == [], (
        f"Strands platform tools should bypass execute_tool_call; got {tools.tool_calls}"
    )
    # The terminal band action means no error event was reported.
    assert not [e for e in tools.events_sent if e["message_type"] == "error"]


@pytest.mark.asyncio
async def test_custom_tool_decision_dispatches_to_handler() -> None:
    """A scripted custom-tool decision reaches the custom handler with validated args (L1)."""

    calls: list[str] = []

    class EchoInput(BaseModel):
        """Echo the given text back."""

        text: str

    async def echo_handler(args: EchoInput) -> str:
        calls.append(args.text)
        return f"echo: {args.text}"

    # Terminal opt-in: the custom tool completes the turn, so no error is reported.
    echo_handler.band_terminal = True  # type: ignore[attr-defined]

    room_id = "strands-spike-custom"
    tools = FakeAgentTools(room_id=room_id)
    adapter = StrandsAdapter(
        model=_ScriptedStrandsModel(
            [
                ("tool", "echo", {"text": "MANGO"}),
                ("text", "done"),
            ]
        ),
        additional_tools=[(EchoInput, echo_handler)],
    )
    await _run(adapter, tools, room_id)

    assert calls == ["MANGO"], f"custom handler not dispatched: {calls}"
    assert tools.messages_sent == []  # the custom tool is not a platform send
    # band_terminal opt-in makes the turn productive: no error event.
    assert not [e for e in tools.events_sent if e["message_type"] == "error"]


@pytest.mark.asyncio
async def test_l6_execution_events_ordered_paired_and_correlated() -> None:
    """Emit.EXECUTION produces tool_call before tool_result, correlated by id (L6)."""
    room_id = "strands-spike-l6"
    tools = FakeAgentTools(room_id=room_id)
    adapter = StrandsAdapter(
        model=_ScriptedStrandsModel(
            [
                (
                    "tool",
                    "band_send_message",
                    {"content": _SEND_CONTENT, "mentions": _SEND_MENTIONS},
                ),
                ("text", "done"),
            ]
        ),
        features=AdapterFeatures(emit={Emit.EXECUTION}),
    )
    await _run(adapter, tools, room_id)

    execution = [
        (e["message_type"], json.loads(e["content"]))
        for e in tools.events_sent
        if e["message_type"] in ("tool_call", "tool_result")
    ]
    assert [kind for kind, _ in execution] == ["tool_call", "tool_result"], (
        f"expected one ordered tool_call/tool_result pair, got {execution}"
    )
    call, result = execution[0][1], execution[1][1]
    assert call["name"] == "band_send_message"
    assert call["args"] == {"content": _SEND_CONTENT, "mentions": _SEND_MENTIONS}
    assert result["name"] == "band_send_message"
    assert result["tool_call_id"] == call["tool_call_id"], (
        f"tool_result not correlated with its tool_call: {execution}"
    )


@pytest.mark.asyncio
async def test_negative_control_text_only_sends_no_message() -> None:
    """A text-only scripted decision dispatches nothing and reports the dropped reply."""
    room_id = "strands-spike-negative"
    tools = FakeAgentTools(room_id=room_id)
    adapter = StrandsAdapter(
        model=_ScriptedStrandsModel([("text", "just a reply, no tools")])
    )
    await _run(adapter, tools, room_id)

    assert tools.messages_sent == [], (
        f"expected no send for a text-only decision, got: {tools.messages_sent}"
    )
    assert tools.tool_calls == []
    # The plain-text answer was silently dropped — the adapter must surface it.
    errors = [e for e in tools.events_sent if e["message_type"] == "error"]
    assert len(errors) == 1, f"expected one error event, got: {tools.events_sent}"
    assert "band_send_message" in errors[0]["content"]
