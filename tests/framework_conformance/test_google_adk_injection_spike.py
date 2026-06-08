"""Tier-1 conformance spike for the Google ADK adapter (INJECTABLE_MODEL_OBJECT family).

WHAT THIS PROVES
----------------
Given a scripted model decision, ADK's real ``InMemoryRunner`` dispatches the tool
through the adapter's real ``_ThenvoiToolBridge`` to
``AgentToolsProtocol.execute_tool_call`` — no live inference, no secrets, no
Google API key.

WHY IT IS HONEST (not circular)
-------------------------------
The faked decision is a scripted ``BaseLlm`` (ADK's framework-native model type)
installed by **instance-substituting the existing ``_create_runner``** method —
the same blessed pattern as Anthropic's ``_call_anthropic``, NOT a test-only
constructor argument (which the contract's declared-seam rule forbids). The
substitute builds the *same* ``ADKAgent`` with the *same* real ADK tools the
adapter would build (``self._build_adk_tools(tools)``), only swapping
``model=self.model`` (a string) for a scripted ``BaseLlm``. Everything downstream
is ADK's real machinery:

    scripted BaseLlm -> InMemoryRunner.run_async -> handle_function_calls_async
      -> tool.run_async -> _ThenvoiToolBridge.run_async
      -> tools.execute_tool_call  (google_adk.py:226)

``model_seam_kind=INTERNAL_MODEL_SUBCLASS`` / ``drift_risk=HIGH``: the scripted
``BaseLlm``/``LlmResponse`` shape is ADK-internal, so this spike is pinned to
``google-adk >=1.10,<1.11``. If a future ADK release reshapes that contract, the spike
fails loudly and the binding's version_pin must be bumped.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

pytest.importorskip("google.adk", reason="google_adk extra not installed")

from google.adk import Agent as ADKAgent  # noqa: E402
from google.adk.models.base_llm import BaseLlm  # noqa: E402
from google.adk.models.llm_response import LlmResponse  # noqa: E402
from google.adk.runners import InMemoryRunner  # noqa: E402
from google.genai import types  # noqa: E402

from thenvoi.adapters.google_adk import GoogleADKAdapter, _sanitize_adk_agent_name  # noqa: E402
from thenvoi.core.protocols import AgentToolsProtocol  # noqa: E402
from thenvoi.core.types import PlatformMessage  # noqa: E402
from thenvoi.testing.fake_tools import FakeAgentTools  # noqa: E402

_APP_NAME = "thenvoi"
_SEND_ARGS: dict[str, Any] = {
    "content": "Injected reply: PINEAPPLE",
    "mentions": ["@tester"],
}


class _ScriptedBaseLlm(BaseLlm):
    """An ADK BaseLlm that replays a fixed script of decisions.

    Each ``generate_content_async`` invocation yields one ``LlmResponse``: either
    a function call (which ADK dispatches to the registered BaseTool) or a final
    text response (which ends the turn).
    """

    script: list[Any]

    @property
    def _llm_type(self) -> str:  # pragma: no cover - identity only
        return "scripted-injection-fake"

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
        fc = types.FunctionCall(name=name, args=args)
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(function_call=fc)])
        )


def _install_scripted_runner(adapter: GoogleADKAdapter, script: list[Any]) -> None:
    """Substitute the declared ``_create_runner`` seam on the instance.

    Builds the SAME ADKAgent the adapter would (same real tools via
    ``_build_adk_tools``), swapping only the model for a scripted BaseLlm.
    """

    def _scripted_create_runner(tools: AgentToolsProtocol) -> InMemoryRunner:
        adk_tools = adapter._build_adk_tools(tools)  # the adapter's REAL tool bridges
        adk_agent = ADKAgent(
            name=_sanitize_adk_agent_name(adapter.agent_name),
            model=_ScriptedBaseLlm(model="scripted", script=list(script)),
            instruction=adapter._system_prompt,
            tools=adk_tools,
        )
        return InMemoryRunner(agent=adk_agent, app_name=_APP_NAME)

    adapter._create_runner = _scripted_create_runner  # type: ignore[method-assign]


class _SchemaTools(FakeAgentTools):
    """Advertises a real send_message schema so the adapter's tool bridge has a
    valid declaration (the bridge still does its real declaration build)."""

    def get_openai_tool_schemas(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "thenvoi_send_message",
                    "description": "Send a message to the chat room.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "mentions": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["content", "mentions"],
                    },
                },
            }
        ]


def _make_msg(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="adk-spike-1",
        room_id=room_id,
        content="Say the magic word",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


async def _run(adapter: GoogleADKAdapter, tools: FakeAgentTools, room_id: str) -> None:
    await adapter.on_started("ADKSpikeBot", "Tier-1 Google ADK injection spike bot.")
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
async def test_scripted_base_llm_routes_to_execute_tool_call() -> None:
    """A scripted BaseLlm function call drives ADK's real runner + tool bridge."""
    room_id = "adk-spike-room"
    tools = _SchemaTools(room_id=room_id)
    adapter = GoogleADKAdapter(model="gemini-2.5-flash")
    _install_scripted_runner(adapter, [("thenvoi_send_message", _SEND_ARGS), None])
    await _run(adapter, tools, room_id)

    dispatched = [
        c for c in tools.tool_calls if c["tool_name"] == "thenvoi_send_message"
    ]
    assert len(dispatched) == 1, (
        f"expected one thenvoi_send_message dispatch through ADK's real runner, "
        f"got: {tools.tool_calls}"
    )
    assert dispatched[0]["arguments"] == _SEND_ARGS, (
        f"args did not survive ADK's real dispatch: {dispatched[0]['arguments']!r}"
    )


@pytest.mark.asyncio
async def test_negative_control_text_only_dispatches_nothing() -> None:
    """A text-only scripted decision dispatches nothing (recorder not vacuous)."""
    room_id = "adk-spike-negative"
    tools = _SchemaTools(room_id=room_id)
    adapter = GoogleADKAdapter(model="gemini-2.5-flash")
    _install_scripted_runner(adapter, [None])
    await _run(adapter, tools, room_id)

    assert tools.tool_calls == [], (
        f"expected no dispatch for a text-only decision, got: {tools.tool_calls}"
    )
