"""Tier-1 conformance spike for the Codex adapter (SCRIPTED_PROTOCOL_CLIENT family).

WHAT THIS PROVES
----------------
Given a *real* Codex ``item/tool/call`` frame — captured verbatim from a live
``codex app-server`` turn — the CodexAdapter's own routing dispatches the tool to
``AgentToolsProtocol.execute_tool_call`` with the right name and args, under no
live inference and no secrets.

WHY IT IS HONEST (not circular)
-------------------------------
The only thing faked is the transport bytes. Everything downstream of the wire
runs for real:

  * the captured frames are the *verbatim* output of a real ``codex app-server``
    turn (see ``fixtures/codex/codex_app_server_tool_call.jsonl``);
  * the replay client subclasses the **real** ``BaseJsonRpcClient`` and pushes the
    captured server frames through the **real** ``_dispatch_rpc_message`` parser,
    so the ``RpcEvent`` the adapter consumes is built by production code, not by
    the test;
  * the adapter's **real** turn loop (``_process_turn_events`` ->
    ``_handle_server_request``) parses ``params['tool'] / ['arguments'] /
    ['callId']`` and dispatches through the real ``execute_tool_call`` path.

This is the make-or-break rule from the Tier-1 injection contract: faking the
model decision must NOT require faking the dispatch. Here the dispatch is real.

``test_captured_tool_call_frame_matches_adapter_contract`` is the schema-pin: it
asserts the *real* parser turns the captured frame into the exact field names the
adapter reads. If a future Codex release renames ``callId`` or reshapes
``arguments``, that test fails loudly and the fixture must be re-captured.

To refresh the fixture, capture a fresh transcript from a live ``codex
app-server`` turn: drive the real ``CodexAdapter`` (via a ``client_factory`` that
wraps ``CodexStdioClient``) and tee every JSON-RPC frame at the two wire choke
points — incoming at ``BaseJsonRpcClient._dispatch_rpc_message`` and outgoing at
``CodexStdioClient._send_json`` — to JSONL, forcing a ``band_send_message``
tool call. Sanitize host-identifying fields before committing. That capture runs
the real app-server and spends tokens, so it is a manual, local step — never CI.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.protocols import AgentToolsProtocol
from band.integrations.codex.types import CodexSessionState
from band.core.types import PlatformMessage
from band.testing.fake_tools import FakeAgentTools
from tests.framework_conformance.codex_replay import (
    ReplayCodexClient,
    captured_tool_call_frame,
    frames_without_tool_call,
    load_codex_replay_frames,
)


class _SendMessageSchemaTools(FakeAgentTools):
    """FakeAgentTools advertising the real send_message schema to the adapter.

    The adapter turns ``get_openai_tool_schemas`` into the ``dynamicTools`` it
    sends on ``thread/start``; the recorder's ``execute_tool_call`` is where the
    dispatch is observed.
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
                            "mentions": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["content", "mentions"],
                    },
                },
            }
        ]


def _make_message(room_id: str) -> PlatformMessage:
    return PlatformMessage(
        id="codex-spike-msg-1",
        room_id=room_id,
        content="call band_send_message",
        sender_id="user-1",
        sender_type="User",
        sender_name="Tester",
        message_type="text",
        metadata=None,
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_real_codex_tool_call_frame_routes_to_execute_tool_call() -> None:
    """A real captured item/tool/call frame drives the adapter's real dispatch."""
    room_id = "codex-spike-room"
    tools = _SendMessageSchemaTools(room_id=room_id)
    frames = load_codex_replay_frames()

    adapter = CodexAdapter(
        config=CodexAdapterConfig(transport="stdio", model="gpt-5.4"),
        client_factory=lambda _config: ReplayCodexClient(frames),
    )
    await adapter.on_started("SpikeBot", "Tier-1 Codex replay spike bot.")
    try:
        await adapter.on_message(
            msg=_make_message(room_id),
            tools=cast("AgentToolsProtocol", tools),
            history=CodexSessionState(room_id=room_id),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id=room_id,
        )
    finally:
        await adapter.on_cleanup(room_id)

    dispatched = [c for c in tools.tool_calls if c["tool_name"] == "band_send_message"]
    assert len(dispatched) == 1, (
        f"expected exactly one band_send_message dispatch from the real "
        f"captured frame, got: {tools.tool_calls}"
    )
    assert dispatched[0]["arguments"] == {
        "content": "CAPTURE_OK",
        "mentions": ["@capturehost"],
    }, f"args did not survive the real parse+route: {dispatched[0]['arguments']!r}"


def test_captured_tool_call_frame_matches_adapter_contract() -> None:
    """Schema-pin: the REAL parser turns the captured frame into the fields the
    adapter reads (codex.py reads params['tool'], ['arguments'], ['callId']).

    Fails loudly if a future Codex release renames or reshapes those fields, which
    is the signal to re-capture the fixture.
    """
    frame = captured_tool_call_frame()
    params = frame["params"]
    # The exact keys the adapter's _handle_server_request consumes.
    assert frame["method"] == "item/tool/call"
    assert frame.get("id") is not None
    assert params["tool"] == "band_send_message"
    assert isinstance(params["arguments"], dict)
    assert set(params["arguments"]) == {"content", "mentions"}
    assert isinstance(params["callId"], str) and params["callId"]


def test_negative_control_no_tool_call_frame_dispatches_nothing() -> None:
    """Falsifiability: a transcript with the tool-call frame removed dispatches
    nothing — proving the recorder observes real routing, not a constant."""
    import asyncio

    room_id = "codex-spike-negative"
    tools = _SendMessageSchemaTools(room_id=room_id)
    frames = frames_without_tool_call()

    adapter = CodexAdapter(
        config=CodexAdapterConfig(transport="stdio", model="gpt-5.4"),
        client_factory=lambda _config: ReplayCodexClient(frames),
    )

    async def _run() -> None:
        await adapter.on_started("SpikeBot", "Tier-1 Codex replay spike bot.")
        try:
            await adapter.on_message(
                msg=_make_message(room_id),
                tools=cast("AgentToolsProtocol", tools),
                history=CodexSessionState(room_id=room_id),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id=room_id,
            )
        finally:
            await adapter.on_cleanup(room_id)

    asyncio.run(_run())

    assert tools.tool_calls == [], (
        f"expected no dispatch when the tool-call frame is absent, got: "
        f"{tools.tool_calls}"
    )
