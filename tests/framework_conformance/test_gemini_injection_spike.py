"""Tier-1 conformance spike for the Gemini adapter (INTERNAL_CLIENT_CALL family).

WHAT THIS PROVES
----------------
Given a scripted model decision, the GeminiAdapter's own tool loop dispatches the
tool to ``AgentToolsProtocol.execute_tool_call`` with the right name and args —
no live inference, no secrets, no Google API key.

WHY IT IS HONEST (not circular)
-------------------------------
The only thing faked is the declared model-call seam: ``_call_gemini`` is
substituted **on the instance** (the blessed pattern, identical to Anthropic's
``_call_anthropic`` in ``test_injection_proof_spike.py``). The substitute returns a
native ``google.genai.types.GenerateContentResponse`` carrying real
``FunctionCall`` parts. Everything downstream runs for real:

  * the adapter's ``while True`` loop in ``on_message`` (``gemini.py:235``);
  * ``response.function_calls`` extraction (``gemini.py:254``);
  * ``_process_function_calls`` -> ``tools.execute_tool_call`` (``gemini.py:501``).

This is the make-or-break rule: faking the model decision must NOT require faking
the dispatch. Here the dispatch is the adapter's real code.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

pytest.importorskip("google.genai", reason="gemini extra not installed")

from google.genai import types  # noqa: E402

from band.adapters.gemini import GeminiAdapter  # noqa: E402
from band.core.protocols import AgentToolsProtocol  # noqa: E402
from band.core.types import PlatformMessage  # noqa: E402
from band.testing.fake_tools import FakeAgentTools  # noqa: E402

_SEND_ARGS: dict[str, Any] = {
    "content": "Injected reply: PINEAPPLE",
    "mentions": ["@tester"],
}


class _SchemaTools(FakeAgentTools):
    """Advertises a real send_message schema so _build_gemini_tools produces a
    valid tool surface (the adapter still does its real tool-schema conversion).
    """

    def get_openai_tool_schemas(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "band_send_message",
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


def _response_with_tool_call(
    name: str, args: dict[str, Any]
) -> types.GenerateContentResponse:
    """A native Gemini response carrying a single function call."""
    part = types.Part(function_call=types.FunctionCall(name=name, args=args, id="fc-1"))
    content = types.Content(role="model", parts=[part])
    candidate = types.Candidate(content=content)
    return types.GenerateContentResponse(candidates=[candidate])


def _text_response(text: str) -> types.GenerateContentResponse:
    content = types.Content(role="model", parts=[types.Part(text=text)])
    candidate = types.Candidate(content=content)
    return types.GenerateContentResponse(candidates=[candidate])


def _install_scripted_call(
    adapter: GeminiAdapter, responses: list[types.GenerateContentResponse]
) -> None:
    """Substitute the declared ``_call_gemini`` seam on the instance."""
    cursor = list(responses)

    async def _scripted(contents: Any, tools: Any) -> types.GenerateContentResponse:
        return cursor.pop(0)

    adapter._call_gemini = _scripted  # type: ignore[method-assign]


def _make_msg(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="gemini-spike-1",
        room_id=room_id,
        content="Say the magic word",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


async def _run(adapter: GeminiAdapter, tools: FakeAgentTools, room_id: str) -> None:
    await adapter.on_started("GeminiSpikeBot", "Tier-1 Gemini injection spike bot.")
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
async def test_scripted_gemini_response_routes_to_execute_tool_call() -> None:
    """A scripted GenerateContentResponse drives the adapter's real dispatch."""
    room_id = "gemini-spike-room"
    tools = _SchemaTools(room_id=room_id)
    adapter = GeminiAdapter(model="gemini-2.5-flash", provider_key="unused-in-spike")
    _install_scripted_call(
        adapter,
        [
            _response_with_tool_call("band_send_message", _SEND_ARGS),
            _text_response("done"),
        ],
    )
    await _run(adapter, tools, room_id)

    dispatched = [c for c in tools.tool_calls if c["tool_name"] == "band_send_message"]
    assert len(dispatched) == 1, (
        f"expected one band_send_message dispatch via real routing, got: {tools.tool_calls}"
    )
    assert dispatched[0]["arguments"] == _SEND_ARGS, (
        f"args did not survive real _process_function_calls: {dispatched[0]['arguments']!r}"
    )


@pytest.mark.asyncio
async def test_negative_control_text_only_dispatches_nothing() -> None:
    """A text-only scripted response produces no tool dispatch (recorder not vacuous)."""
    room_id = "gemini-spike-negative"
    tools = _SchemaTools(room_id=room_id)
    adapter = GeminiAdapter(model="gemini-2.5-flash", provider_key="unused-in-spike")
    _install_scripted_call(adapter, [_text_response("just a reply, no tools")])
    await _run(adapter, tools, room_id)

    assert tools.tool_calls == [], (
        f"expected no dispatch for a text-only decision, got: {tools.tool_calls}"
    )
