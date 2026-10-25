from __future__ import annotations

import json

from band.adapters.opencode import OpencodeAdapter
from band.core.types import Capability, Emit
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import FakeAgentTools, events_of_type
from tests.adapters.audiences import ROOM_MEMBERS
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    FakeOpencodeClient,
    event_message_updated,
    event_session_idle,
    event_tool_part,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)


class TestToolReports:
    async def test_reports_tool_events_when_enabled(
        self,
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="bash",
                        call_id="call-1",
                        status="running",
                        input_data={"command": "pytest"},
                    ),
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="bash",
                        call_id="call-1",
                        status="completed",
                        input_data={"command": "pytest"},
                        output="ok",
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            client_factory=lambda _config: fake_client,
            emit=Emit.TOOL_CALLS,
        )
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_calls = events_of_type(tools, "tool_call")
        tool_results = events_of_type(tools, "tool_result")
        assert len(tool_calls) == 1
        assert len(tool_results) == 1
        assert json.loads(tool_calls[0]["content"])["name"] == "bash"
        assert json.loads(tool_results[0]["content"])["name"] == "bash"
        assert json.loads(tool_results[0]["content"])["output"] == "ok"

    async def test_reports_tool_call_args_from_first_non_pending_frame(
        self, make_adapter, tools
    ) -> None:
        """OpenCode's first frame for a tool part is always PENDING with an empty
        ``input`` -- arguments only appear once the part moves past PENDING. The
        single tool_call report (fired once per call_id) must land on that later
        frame, not the empty first one.
        """
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="band_create_task",
                        call_id="call-1",
                        status="pending",
                        input_data={},
                    ),
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="band_create_task",
                        call_id="call-1",
                        status="running",
                        input_data={"subject": "write tests"},
                    ),
                    event_tool_part(
                        "sess-1",
                        "msg-4",
                        tool="band_create_task",
                        call_id="call-1",
                        status="completed",
                        input_data={"subject": "write tests"},
                        output="task-1",
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client, emit=Emit.TOOL_CALLS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_calls = events_of_type(tools, "tool_call")
        assert len(tool_calls) == 1
        assert json.loads(tool_calls[0]["content"])["args"] == {
            "subject": "write tests"
        }

    async def test_preserves_falsy_tool_result_outputs_when_reporting(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_tool_part(
                        "sess-1",
                        "msg-7",
                        tool="bash",
                        call_id="call-2",
                        status="completed",
                        input_data={"command": "printf 0"},
                        output=0,
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = OpencodeAdapter(
            client_factory=lambda _config: fake_client,
            emit=Emit.TOOL_CALLS,
        )
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_results = events_of_type(tools, "tool_result")
        assert len(tool_results) == 1
        assert json.loads(tool_results[0]["content"])["output"] == 0

    async def test_reports_is_error_on_a_failed_tool_result(
        self, make_adapter, tools
    ) -> None:
        """A tool part that ends in ``error`` must set ``is_error`` on the reported
        tool_result -- consumers gate on that flag, not on inspecting ``output``."""
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_tool_part(
                        "sess-1",
                        "msg-8",
                        tool="bash",
                        call_id="call-3",
                        status="error",
                        input_data={"command": "false"},
                        error="command failed",
                    ),
                    event_session_idle("sess-1"),
                ]
            ]
        )
        adapter = make_adapter(fake_client, emit=Emit.TOOL_CALLS)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        await deliver_message(
            adapter,
            make_platform_message(),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        tool_results = events_of_type(tools, "tool_result")
        assert len(tool_results) == 1
        content = json.loads(tool_results[0]["content"])
        assert content["is_error"] is True
        assert content["output"] == {"error": "command failed"}

    async def test_tool_reports_canonicalize_server_prefixed_names(
        self, make_adapter, tools
    ) -> None:
        """OpenCode surfaces a remote MCP server's tools as `{server}_{tool}`
        (band_store_memory arrives as band_band_store_memory); reported
        tool_call events must carry the canonical band name so consumers
        match one vocabulary across all adapters."""
        fake_client = FakeOpencodeClient()
        adapter = OpencodeAdapter(
            client_factory=lambda _config: fake_client,
            capabilities=Capability.MEMORY,
            emit=Emit.TOOL_CALLS,
        )
        await adapter.on_started("OpenCode Agent", "A coding agent")
        # OpenCode prefixes the band MCP tool with the agent-scoped server name
        # (band_store_memory -> {server}_band_store_memory).
        prefixed = f"{adapter._mcp_server_name}_band_store_memory"
        fake_client._prompt_event_sequences = [
            [
                event_message_updated("sess-1", "msg-1"),
                event_tool_part(
                    "sess-1",
                    "msg-1",
                    tool=prefixed,
                    call_id="call-1",
                    status="running",
                    input_data={"content": "note"},
                ),
                event_tool_part(
                    "sess-1",
                    "msg-1",
                    tool=prefixed,
                    call_id="call-1",
                    status="completed",
                    input_data={"content": "note"},
                    output="stored",
                ),
                event_session_idle("sess-1"),
            ]
        ]
        tools = FakeAgentTools(participants=ROOM_MEMBERS)

        await run_single_turn(adapter, tools)

        tool_calls = [
            json.loads(e["content"]) for e in events_of_type(tools, "tool_call")
        ]
        tool_results = [
            json.loads(e["content"]) for e in events_of_type(tools, "tool_result")
        ]
        assert [c["name"] for c in tool_calls] == ["band_store_memory"]
        assert [r["name"] for r in tool_results] == ["band_store_memory"]
