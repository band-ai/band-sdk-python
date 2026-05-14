from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from crewai.flow.flow import Flow, start
from pydantic import BaseModel

from thenvoi.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    HistoryCrewAIFlowStateSource,
    get_current_flow_runtime,
)
from thenvoi.core.types import PlatformMessage
from thenvoi.testing.fake_tools import FakeAgentTools


class EmailsInput(BaseModel):
    """Return the user's recent emails."""


@pytest.mark.asyncio
async def test_real_crewai_flow_can_call_adapter_registered_custom_tool() -> None:
    calls: list[str] = []

    def emails() -> str:
        calls.append("emails")
        return "real crewai flow inbox text"

    class InboxFlow(Flow):
        @start()
        async def decide(self) -> dict[str, Any]:
            runtime = get_current_flow_runtime()
            assert runtime is not None
            result = await runtime.tools.emails()
            via_name = await runtime.call_tool("emails")
            assert result == via_name == "real crewai flow inbox text"
            return {"decision": "direct_response", "content": result, "mentions": []}

    msg = PlatformMessage(
        id="msg-real-flow",
        room_id="room-real-flow",
        content="build a deck from my emails",
        sender_id="user-real",
        sender_type="User",
        sender_name="Pat",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )
    adapter = CrewAIFlowAdapter(
        flow_factory=InboxFlow,
        state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        additional_tools=[(EmailsInput, emails)],
    )
    tools = FakeAgentTools(room_id="room-real-flow")

    await adapter.on_started("agent-real", "Real Flow validation")
    await adapter.on_message(
        msg=msg,
        tools=tools,
        history=None,  # type: ignore[arg-type]
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-real-flow",
    )

    assert calls == ["emails", "emails"]
    assert tools.messages_sent == [
        {
            "id": "msg-0",
            "content": "real crewai flow inbox text",
            "mentions": ["user-real"],
        }
    ]
