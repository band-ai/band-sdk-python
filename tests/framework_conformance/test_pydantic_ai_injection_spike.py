"""Tier-1 conformance spike for the PydanticAI adapter (INJECTABLE_MODEL_OBJECT family).

WHAT THIS PROVES
----------------
Given a scripted model decision, the PydanticAIAdapter's real ``agent.tool``
wrappers dispatch the tool to the platform via ``AgentToolsProtocol`` — no live
inference, no secrets, no provider API key.

WHY IT IS HONEST (not circular)
-------------------------------
The faked decision is installed via PydanticAI's **public** test facility:
``Agent.override(model=FunctionModel(...))``. ``FunctionModel`` is shipped by the
framework for exactly this purpose, so the scripted surface is a stable public
contract (``model_seam_kind=PUBLIC_TEST_MODEL``). No production code changes; the
adapter builds ``self._agent`` once in ``on_started`` and the override wraps the
real ``run_stream_events`` call.

OBSERVATION PATH (the load-bearing detail)
------------------------------------------
PydanticAI's platform-tool wrappers call **typed** ``AgentToolsProtocol`` methods
directly — ``thenvoi_send_message`` calls ``ctx.deps.send_message(...)``
(``pydantic_ai.py:168``), NOT ``execute_tool_call``. So dispatch is observed on
``FakeAgentTools.messages_sent``, not ``.tool_calls``. This is exactly the
``observation_paths`` distinction the contract's registry encodes: a canary that
only watched ``tool_calls`` would wrongly fail PydanticAI.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

import pytest

pytest.importorskip("pydantic_ai", reason="pydantic-ai extra not installed")

from pydantic_ai.messages import ModelMessage  # noqa: E402
from pydantic_ai.models.function import (  # noqa: E402
    AgentInfo,
    DeltaToolCall,
    FunctionModel,
)

from thenvoi.adapters.pydantic_ai import PydanticAIAdapter  # noqa: E402
from thenvoi.core.protocols import AgentToolsProtocol  # noqa: E402
from thenvoi.core.types import PlatformMessage  # noqa: E402
from thenvoi.testing.fake_tools import FakeAgentTools  # noqa: E402

_SEND_CONTENT = "Injected reply: PINEAPPLE"
_SEND_MENTIONS = ["@tester"]


@pytest.fixture(autouse=True)
def _dummy_openai_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """PydanticAI's Agent('openai:...') eagerly constructs an OpenAI client in
    on_started, which requires a key to *exist*. The key is never USED: the
    ``Agent.override(model=FunctionModel(...))`` replaces the model entirely, so
    no real inference happens. A dummy value keeps the spike hermetic.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-spike-not-used")


def _make_msg(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="pyai-spike-1",
        room_id=room_id,
        content="Say the magic word",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


def _scripted_model(turns: list[Any]) -> FunctionModel:
    """A streaming FunctionModel that replays one decision per invocation.

    The PydanticAIAdapter drives the agent via ``run_stream_events``, so the
    model must support streaming (a plain ``function`` is rejected by
    ``FunctionModel``). Each invocation yields one decision:

    * a ``DeltaToolCalls`` dict -> emits a tool call the agent dispatches;
    * a ``str`` -> emits text and ends the run.

    ``turns`` items are either ("tool", name, json_args) or ("text", body).
    """
    cursor = list(turns)

    async def _stream(messages: list[ModelMessage], info: AgentInfo) -> Any:
        decision = cursor.pop(0) if cursor else ("text", "done")
        if decision[0] == "tool":
            _, name, json_args = decision
            yield {
                0: DeltaToolCall(name=name, json_args=json_args, tool_call_id="call-1")
            }
        else:
            yield decision[1]

    return FunctionModel(stream_function=_stream)


async def _run_with_model(
    adapter: PydanticAIAdapter,
    tools: FakeAgentTools,
    room_id: str,
    model: FunctionModel,
) -> None:
    await adapter.on_started("PyAISpikeBot", "Tier-1 PydanticAI injection spike bot.")
    assert adapter._agent is not None, "adapter._agent must be built in on_started"
    # Install the scripted decision at the public override seam — no ctor change.
    with adapter._agent.override(model=model):
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
async def test_scripted_function_model_routes_to_typed_send_message() -> None:
    """A scripted FunctionModel tool call drives the real agent.tool wrapper.

    Observed on messages_sent (typed-method dispatch), NOT tool_calls.
    """
    room_id = "pyai-spike-room"
    tools = FakeAgentTools(room_id=room_id)
    adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")

    model = _scripted_model(
        [
            (
                "tool",
                "thenvoi_send_message",
                json.dumps({"content": _SEND_CONTENT, "mentions": _SEND_MENTIONS}),
            ),
            ("text", "done"),
        ]
    )
    await _run_with_model(adapter, tools, room_id, model)

    # PydanticAI dispatches platform tools through typed AgentToolsProtocol
    # methods, so observe on messages_sent (the contract's observation_paths).
    assert len(tools.messages_sent) == 1, (
        f"expected one send_message dispatch via the real agent.tool wrapper, "
        f"got messages_sent={tools.messages_sent}, tool_calls={tools.tool_calls}"
    )
    assert tools.messages_sent[0]["content"] == _SEND_CONTENT
    assert tools.messages_sent[0]["mentions"] == _SEND_MENTIONS
    # And confirm it did NOT route through execute_tool_call (the dual-path proof).
    assert tools.tool_calls == [], (
        f"PydanticAI platform tools should bypass execute_tool_call; got {tools.tool_calls}"
    )


@pytest.mark.asyncio
async def test_negative_control_text_only_sends_no_message() -> None:
    """A text-only scripted decision dispatches nothing (recorder not vacuous)."""
    room_id = "pyai-spike-negative"
    tools = FakeAgentTools(room_id=room_id)
    adapter = PydanticAIAdapter(model="openai:gpt-4o-mini")

    model = _scripted_model([("text", "just a reply, no tools")])
    await _run_with_model(adapter, tools, room_id, model)

    assert tools.messages_sent == [], (
        f"expected no send for a text-only decision, got: {tools.messages_sent}"
    )
    assert tools.tool_calls == []
