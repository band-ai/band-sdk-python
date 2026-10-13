"""Codex adapter flow: native approvals and internal operations reach the room."""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.protocols import TurnResultAlreadyReported
from band.core.types import Emit
from tests.adapters.codex.kit import ToolSchemaFakeTools, agent_input
from tests.adapters.codexturns import (
    FakeCodexClient,
    await_released_turn,
    event_notification,
    event_request,
    make_codex_adapter,
)
from tests.adapters.dispatch import deliver_event


class TestNativeActivityRelay:
    """Native approvals and internal operations reach the room."""

    @pytest.mark.asyncio
    async def test_manual_approval_resolved_by_out_of_band_approve_command(
        self,
    ) -> None:
        tools = ToolSchemaFakeTools()
        fake_client = FakeCodexClient(
            events=[
                event_request(
                    7,
                    "item/commandExecution/requestApproval",
                    {"approvalId": "ap-1", "command": ["git", "push"]},
                ),
                event_notification(
                    "turn/completed",
                    {
                        "turn": {
                            "id": "turn-1",
                            "status": "completed",
                            "items": [],
                            "error": None,
                        }
                    },
                ),
            ]
        )
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(
                approval_mode="manual", approval_wait_timeout_s=30.0
            ),
        )
        await adapter.on_started("Codex Agent", "Integration test agent")

        first_turn = asyncio.create_task(
            deliver_event(
                adapter,
                agent_input(
                    "run protected command",
                    tools,
                    room_id="room-1",
                    is_session_bootstrap=True,
                ),
            )
        )

        start = time.monotonic()
        while time.monotonic() - start < 2.0:
            if any(
                "Approval id: `ap-1`" in msg["content"] for msg in tools.messages_sent
            ):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Approval notification not sent within timeout")

        await deliver_event(
            adapter,
            agent_input(
                "/approve ap-1",
                tools,
                room_id="room-1",
                is_session_bootstrap=False,
            ),
        )
        await asyncio.wait_for(first_turn, timeout=2.0)
        # on_event returned once the room was asked; the turn itself runs on.
        await await_released_turn(adapter, "room-1", timeout_s=2.0)

        assert (7, {"decision": "accept"}) in fake_client.responses
        assert any(
            "Approval `ap-1` resolved as `accept`." in msg["content"]
            for msg in tools.messages_sent
        )

    @pytest.mark.asyncio
    async def test_item_completed_forwards_internal_operations(self) -> None:
        """Full cycle: commandExecution + fileChange + agentMessage items all forwarded."""

        tools = ToolSchemaFakeTools()
        fake_client = FakeCodexClient(
            events=[
                event_notification(
                    "item/completed",
                    {
                        "item": {
                            "type": "commandExecution",
                            "id": "cmd-1",
                            "command": "pytest tests/",
                            "cwd": "/workspace",
                            "aggregated_output": "5 passed",
                            "exitCode": 0,
                        }
                    },
                ),
                event_notification(
                    "item/completed",
                    {
                        "item": {
                            "type": "fileChange",
                            "id": "fc-1",
                            "changes": [{"path": "src/app.py"}],
                            "status": "applied",
                        }
                    },
                ),
                event_notification(
                    "item/completed",
                    {
                        "item": {
                            "type": "reasoning",
                            "id": "reason-1",
                            "summary": ["Tests pass, applying fix"],
                        }
                    },
                ),
                event_notification(
                    "item/completed",
                    {
                        "item": {
                            "type": "agentMessage",
                            "id": "msg-1",
                            "text": "Fixed the bug and all tests pass.",
                        }
                    },
                ),
                event_notification(
                    "turn/completed",
                    {
                        "turn": {
                            "id": "turn-1",
                            "status": "completed",
                            "items": [],
                            "error": None,
                        }
                    },
                ),
            ]
        )
        adapter = make_codex_adapter(
            fake_client,
            config=CodexAdapterConfig(),
            emit=Emit.TOOL_CALLS | Emit.THOUGHTS,
        )
        await adapter.on_started("Codex Agent", "Integration test agent")
        with pytest.raises(TurnResultAlreadyReported):
            await adapter.on_event(
                agent_input(
                    "fix the failing test",
                    tools,
                    room_id="room-ops",
                    is_session_bootstrap=True,
                )
            )

        # Verify tool events for commandExecution
        tool_call_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_call"
        ]
        tool_result_events = [
            e for e in tools.events_sent if e["message_type"] == "tool_result"
        ]
        # commandExecution + fileChange = 2 tool_call + 2 tool_result
        assert len(tool_call_events) == 2
        assert len(tool_result_events) == 2

        exec_call = json.loads(tool_call_events[0]["content"])
        assert exec_call["name"] == "exec"
        assert exec_call["args"]["command"] == "pytest tests/"

        file_call = json.loads(tool_call_events[1]["content"])
        assert file_call["name"] == "file_edit"

        # Verify thought event for reasoning
        thought_events = [
            e for e in tools.events_sent if e["message_type"] == "thought"
        ]
        assert len(thought_events) == 2
        assert "Tests pass" in thought_events[0]["content"]

        # Verify final text message from agentMessage
        assert tools.messages_sent == []
