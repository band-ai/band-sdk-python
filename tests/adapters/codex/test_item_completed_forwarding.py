"""Codex adapter: tests for forwarding internal Codex operations as platform events."""

from __future__ import annotations

import json

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.core.types import Emit
from band.testing import events_of_type
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    run_codex_turn,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    tool_call_request,
    turn_completed,
)


class TestItemCompletedForwarding:
    """Tests for forwarding internal Codex operations as platform events."""

    @pytest.mark.asyncio
    async def test_item_completed_mcpToolCall_send_room_file_redacts_content(
        self,
    ) -> None:
        """band_send_room_file routed through Codex's own mcpToolCall item
        (a separate reporting path from item/tool/call, keyed by the bare
        "tool" field before it's wrapped in the "mcp:{server}/{tool}"
        display name) must also redact raw file content before it reaches a
        tool_call event."""
        raw_content = "raw file bytes that must never reach a tool_call event"
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "mcpToolCall",
                        "id": "mcp-1",
                        "server": "band",
                        "tool": "band_send_room_file",
                        "arguments": {"content": raw_content, "filename": "f.txt"},
                        "result": {"status": "success"},
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        call_data = json.loads(events_of_type(tools, "tool_call")[0]["content"])
        assert call_data["name"] == "mcp:band/band_send_room_file"
        assert (
            call_data["args"]["content"]
            == f"<{len(raw_content.encode('utf-8'))} byte file content>"
        )
        assert raw_content not in json.dumps(call_data)

    @pytest.mark.asyncio
    async def test_item_completed_commandExecution_emits_tool_events(self) -> None:
        """commandExecution item emits tool_call + tool_result with command/output."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "ls -la",
                        "cwd": "/workspace",
                        "aggregated_output": "total 42\ndrwxr-xr-x ...",
                        "exitCode": 0,
                        "status": "completed",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "exec"
        assert call_data["args"]["command"] == "ls -la"
        assert call_data["args"]["cwd"] == "/workspace"
        assert call_data["tool_call_id"] == "cmd-1"

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["name"] == "exec"
        assert "total 42" in result_data["output"]
        assert "exit_code=0" in result_data["output"]
        assert result_data["tool_call_id"] == "cmd-1"

    @pytest.mark.asyncio
    async def test_item_completed_fileChange_emits_tool_events(self) -> None:
        """fileChange emits tool_call + tool_result with file paths."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "fileChange",
                        "id": "fc-1",
                        "changes": [
                            {"path": "src/main.py"},
                            {"path": "src/utils.py"},
                        ],
                        "status": "applied",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "file_edit"
        assert call_data["args"]["files"] == ["src/main.py", "src/utils.py"]

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] == "applied"

    @pytest.mark.asyncio
    async def test_item_completed_fileChange_missing_changes_is_safe(self) -> None:
        """fileChange without changes list should not crash and emits empty files."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "fileChange",
                        "id": "fc-2",
                        "changes": None,
                        "status": "applied",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        assert len(tool_call_events) == 1
        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "file_edit"
        assert call_data["args"]["files"] == []

    @pytest.mark.asyncio
    async def test_item_completed_imageView_emits_tool_events(self) -> None:
        """imageView emits tool_call + tool_result."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "imageView",
                        "id": "img-1",
                        "path": "/tmp/screenshot.png",
                        "status": "viewed",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "view_image"
        assert call_data["args"]["path"] == "/tmp/screenshot.png"

    @pytest.mark.asyncio
    async def test_item_completed_collabAgentToolCall_emits_tool_events(self) -> None:
        """collabAgentToolCall emits tool_call + tool_result preserving empty result."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "collabAgentToolCall",
                        "id": "collab-1",
                        "tool": "delegate",
                        "prompt": "Review the changes",
                        "agents": ["Reviewer-1", "Reviewer-2"],
                        "result": {},
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "collab:delegate"
        assert call_data["args"]["prompt"] == "Review the changes"
        assert call_data["args"]["agents"] == ["Reviewer-1", "Reviewer-2"]

        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] == "{}"

    @pytest.mark.asyncio
    async def test_item_completed_collabAgentToolCall_non_text_list_result_preserves_data(
        self,
    ) -> None:
        """A non-text list result is dumped as JSON, not collapsed to "completed"."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "collabAgentToolCall",
                        "id": "collab-2",
                        "tool": "delegate",
                        "result": [1, 2, 3],
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_result_events) == 1
        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] == "[1, 2, 3]"

    @pytest.mark.asyncio
    async def test_item_completed_mcpToolCall_emits_tool_events(self) -> None:
        """mcpToolCall emits tool_call + tool_result with server/tool name."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "mcpToolCall",
                        "id": "mcp-1",
                        "server": "filesystem",
                        "tool": "read_file",
                        "arguments": {"path": "/etc/hosts"},
                        "result": {"content": "127.0.0.1 localhost"},
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1

        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "mcp:filesystem/read_file"
        assert call_data["args"]["path"] == "/etc/hosts"

        result_data = json.loads(tool_result_events[0]["content"])
        assert "127.0.0.1 localhost" in result_data["output"]

    @pytest.mark.asyncio
    async def test_item_completed_mcpToolCall_non_text_list_result_preserves_data(
        self,
    ) -> None:
        """A non-text list result (e.g. an MCP image content block) is dumped as
        JSON, not collapsed to the generic "completed" status.

        Unlike thought extraction, a tool-call result is
        real data even when it isn't textual — ``_stringify_tool_output`` must
        use its ``raw_fallback`` mode here so nothing is silently discarded.
        """
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "mcpToolCall",
                        "id": "mcp-2",
                        "server": "filesystem",
                        "tool": "read_image",
                        "arguments": {},
                        "result": [
                            {"type": "image", "data": "abc123", "mimeType": "image/png"}
                        ],
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_result_events) == 1
        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] != "completed"
        assert "image/png" in result_data["output"]

    @pytest.mark.asyncio
    async def test_a_dynamic_tool_call_is_reported_once(self) -> None:
        """Codex completes the item it already requested via item/tool/call."""
        events = [
            tool_call_request(42, "band_lookup_peers"),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "dynamicToolCall",
                        "tool": "band_lookup_peers",
                        "arguments": {},
                        "status": "completed",
                    }
                },
            ),
            turn_completed(),
        ]

        turn = await run_codex_turn(
            events=events, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )

        reported = [event["message_type"] for event in turn.tools.events_sent]
        assert reported == ["tool_call", "tool_result"]

    @pytest.mark.asyncio
    async def test_item_completed_reasoning_emits_thought(self) -> None:
        """reasoning item emits thought event when emit=Emit.THOUGHTS."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-1",
                        "summary": [
                            "Analyzing the codebase structure",
                            "Identified key files to modify",
                        ],
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.THOUGHTS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        thought_events = events_of_type(tools, "thought")
        assert len(thought_events) == 1
        assert "Analyzing the codebase structure" in thought_events[0]["content"]
        assert "Identified key files to modify" in thought_events[0]["content"]

    @pytest.mark.asyncio
    async def test_item_completed_dict_summary_text_emits_thought(self) -> None:
        """Reasoning summary entries shaped as {text: ...} use stringify SSOT."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-dict",
                        "summary": [
                            {"type": "summary_text", "text": "Weighing the tradeoffs"},
                            {"type": "summary_text", "text": "Choosing the safer joke"},
                        ],
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit={Emit.THOUGHTS}
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        thought_events = events_of_type(tools, "thought")
        assert len(thought_events) == 1
        assert "Weighing the tradeoffs" in thought_events[0]["content"]
        assert "Choosing the safer joke" in thought_events[0]["content"]

    @pytest.mark.asyncio
    async def test_item_completed_empty_reasoning_summary_skips_thought(self) -> None:
        """Empty reasoning summaries must not post a '(reasoning)' placeholder."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-empty",
                        "summary": [],
                    }
                },
            ),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-blank",
                        "summary": ["", "  "],
                    }
                },
            ),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-none",
                        "summary": None,
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit={Emit.THOUGHTS}
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        thought_events = events_of_type(tools, "thought")
        assert thought_events == []

    @pytest.mark.asyncio
    async def test_item_completed_empty_plan_text_skips_thought(self) -> None:
        """Empty plan text must not post a '(plan)' placeholder."""
        events = [
            event_notification(
                "item/completed",
                {"item": {"type": "plan", "id": "plan-empty", "text": ""}},
            ),
            event_notification(
                "item/completed",
                {"item": {"type": "plan", "id": "plan-blank", "text": "   "}},
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit={Emit.THOUGHTS}
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        thought_events = events_of_type(tools, "thought")
        assert thought_events == []

    @pytest.mark.asyncio
    async def test_item_completed_skipped_when_reporting_disabled(self) -> None:
        """No tool events when emit narrows to Emit.TASK_EVENTS only."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "ls",
                        "exitCode": 0,
                    }
                },
            ),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "reasoning",
                        "id": "reason-1",
                        "summary": ["thinking"],
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TASK_EVENTS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_events = [
            e
            for e in tools.events_sent
            if e["message_type"] in {"tool_call", "tool_result", "thought"}
        ]
        assert tool_events == []

    @pytest.mark.asyncio
    async def test_completed_agent_message_is_emitted_as_a_thought(self) -> None:
        """Existing agentMessage behavior preserved alongside new forwarding."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "pytest",
                        "exitCode": 0,
                        "aggregated_output": "all tests passed",
                    }
                },
            ),
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "agentMessage",
                        "id": "msg-1",
                        "text": "All tests pass!",
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        # agentMessage text should still be sent as the final message
        assert tools.messages_sent == []
        assert events_of_type(tools, "thought") == []
        assert not tools.turn.complete
        # commandExecution should also be forwarded as tool events
        tool_call_events = events_of_type(tools, "tool_call")
        assert len(tool_call_events) == 1

    @pytest.mark.asyncio
    async def test_item_completed_webSearch_emits_tool_events(self) -> None:
        """webSearch item emits tool_call + tool_result."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "webSearch",
                        "id": "ws-1",
                        "query": "python asyncio tutorial",
                        "action": {"url": "https://example.com", "title": "Tutorial"},
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        assert len(tool_call_events) == 1
        call_data = json.loads(tool_call_events[0]["content"])
        assert call_data["name"] == "web_search"
        assert call_data["args"]["query"] == "python asyncio tutorial"

    @pytest.mark.asyncio
    async def test_item_completed_webSearch_non_text_list_action_preserves_data(
        self,
    ) -> None:
        """A non-text list action is dumped as JSON, not collapsed to "completed"."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "webSearch",
                        "id": "ws-2",
                        "query": "python asyncio tutorial",
                        "action": [{"url": "https://example.com"}],
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_result_events = events_of_type(tools, "tool_result")
        assert len(tool_result_events) == 1
        result_data = json.loads(tool_result_events[0]["content"])
        assert result_data["output"] == '[{"url": "https://example.com"}]'

    @pytest.mark.asyncio
    async def test_item_completed_metadata_includes_codex_ids(self) -> None:
        """Forwarded events include codex_room_id, codex_thread_id, codex_turn_id."""
        events = [
            event_notification(
                "item/completed",
                {
                    "item": {
                        "type": "commandExecution",
                        "id": "cmd-1",
                        "command": "echo hi",
                        "exitCode": 0,
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(), emit=Emit.TOOL_CALLS
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        tool_call_events = events_of_type(tools, "tool_call")
        assert len(tool_call_events) == 1
        meta = tool_call_events[0]["metadata"]
        assert meta["codex_room_id"] == ROOM_ID
        assert meta["codex_thread_id"] == "thr-1"
        assert meta["codex_turn_id"] == "turn-1"
