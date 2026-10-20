from __future__ import annotations

import json

from agno.run.agent import RunOutput

from band.core.types import Emit
from tests.adapters.agno.helpers import (
    tool_events,
    tool_execution,
)


class TestEmitExecution:
    async def test_emits_tool_call_and_result_events(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # Streamed run: started + completed events for one tool call. Reporting
        # is live (during the run), driven off Agno's native stream events.
        events = tool_events(
            tool_execution("band_lookup_peers", args={"page": "1"}, result="ok")
        )
        adapter, _ = await make_started_adapter(emit=Emit.TOOL_CALLS, events=events)

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        types = [e["message_type"] for e in tools.events_sent]
        assert types == ["tool_call", "tool_result"]
        call_payload = json.loads(tools.events_sent[0]["content"])
        result_payload = json.loads(tools.events_sent[1]["content"])
        assert call_payload == {
            "name": "band_lookup_peers",
            "args": {"page": "1"},
            "tool_call_id": "tc_1",
        }
        assert result_payload["output"] == "ok"
        assert result_payload["is_error"] is False

    async def test_band_send_message_tool_call_is_reported(
        self, make_started_adapter, sample_platform_message, tools
    ):
        """band_send_message is reported like any other tool call — no suppression."""
        events = tool_events(tool_execution("band_send_message"))
        adapter, _ = await make_started_adapter(emit=Emit.TOOL_CALLS, events=events)

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        types = [e["message_type"] for e in tools.events_sent]
        assert types == ["tool_call", "tool_result"]

    async def test_send_room_file_tool_call_event_redacts_content(
        self, make_started_adapter, sample_platform_message, tools
    ):
        """band_send_room_file's tool_call event must report a bounded
        placeholder for content, not the raw file bytes -- generic ARGS
        reporting has no idea this one tool's content argument can carry up
        to MAX_SEND_CONTENT_BYTES of real file data."""
        events = tool_events(
            tool_execution(
                "band_send_room_file",
                args={"content": "raw file bytes", "filename": "notes.txt"},
                result="ok",
            )
        )
        adapter, _ = await make_started_adapter(emit=Emit.TOOL_CALLS, events=events)

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        call_payload = json.loads(tools.events_sent[0]["content"])
        assert call_payload["args"]["content"] == "<14 byte file content>"
        assert call_payload["args"]["filename"] == "notes.txt"

    async def test_no_events_without_execution_emit(
        self, make_started_adapter, sample_platform_message, tools
    ):
        # emit=() -> non-streaming run, no live reporting. The final
        # RunOutput still carries executions, but nothing is emitted.
        response = RunOutput(tools=[tool_execution("band_lookup_peers")])
        adapter, agent = await make_started_adapter(response, emit=())

        await adapter.on_message(
            sample_platform_message,
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.events_sent == []
        agent.arun.assert_called_once()
