"""Manual Codex approval notices must not replace the turn's final reply."""

from __future__ import annotations

from band.core.turn.oneshot import run_adapter_turn

import pytest

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.core.run.context import SimpleRunContext
from tests.core.adapterhelpers import make_agent_input
from tests.adapters.test_codex_adapter import (
    FakeCodexClient,
    ToolSchemaFakeTools,
    _event_notification,
    _event_request,
    make_platform_message,
)


@pytest.mark.asyncio
async def test_manual_approval_notice_keeps_the_final_reply() -> None:
    final_answer = "Final answer."
    events = [
        _event_request(
            7,
            "item/commandExecution/requestApproval",
            {"command": "rm -rf tmp"},
        ),
        _event_notification(
            "item/completed",
            {"item": {"type": "agentMessage", "text": final_answer}},
        ),
        _event_notification(
            "turn/completed",
            {"turn": {"id": "turn-1", "status": "completed", "items": []}},
        ),
    ]
    adapter = CodexAdapter(
        config=CodexAdapterConfig(
            transport="ws",
            approval_mode="manual",
            approval_wait_timeout_s=0.01,
        ),
        client_factory=lambda _config: FakeCodexClient(events=events),
    )
    tools = ToolSchemaFakeTools()

    await adapter.on_started("Codex Agent", "A coding agent")
    await run_adapter_turn(
        adapter,
        make_agent_input(msg=make_platform_message(), tools=tools),
        context=SimpleRunContext(tools=tools),
    )

    assert [message["content"] for message in tools.messages_sent][-1] == final_answer
