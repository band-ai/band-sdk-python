from __future__ import annotations

from typing import Any

import pytest

from band.adapters.crewai_flow import (
    CrewAIFlowAdapter,
    HistoryCrewAIFlowStateSource,
)
from band.testing.fake_tools import FakeAgentTools
from tests.adapters.crewaiflow.kit import message


class TestFlowInputs:
    @pytest.mark.asyncio
    async def test_inputs_include_current_message_and_requester(self) -> None:
        captured: dict[str, Any] = {}

        class CapturingFlow:
            async def kickoff_async(self, inputs: dict | None = None) -> dict:
                captured.update(inputs or {})
                return {"decision": "waiting", "reason": "captured"}

        adapter = CrewAIFlowAdapter(
            flow_factory=CapturingFlow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("router", "")
        tools = FakeAgentTools()
        msg = message(id="msg-request", content="build my brief")
        await adapter.on_message(
            msg=msg,
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert captured["message"] == captured["current_message"]
        assert captured["current_message"] == {
            "id": "msg-request",
            "content": "build my brief",
            "sender_id": "user-1",
            "sender_name": "Pat",
            "sender_type": "User",
            "message_type": "text",
            "created_at": msg.created_at.isoformat(),
        }
        assert captured["requester"] == {
            "id": "user-1",
            "name": "Pat",
            "type": "User",
        }
