from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest


class TestToolExecution:
    def test_tool_returns_error_without_room_context(self, CrewAIAdapter, crewai_mocks):
        """Tools return error when called outside message handling (no context set)."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_message_tool = next(t for t in tools if t.name == "band_send_message")

        # Call tool without setting context variable (simulates call outside message handling)
        result = send_message_tool._run(content="Hello!", mentions=[])

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "No room context available" in result_data["message"]

    @pytest.mark.asyncio
    async def test_all_tools_have_correct_schemas(self, CrewAIAdapter, crewai_mocks):
        """Tools no longer require room_id - context is managed via context variable."""
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        # band_send_message should have content and mentions, but NOT room_id
        send_message = next(t for t in tools if t.name == "band_send_message")
        assert send_message.args_schema is not None
        schema_fields = send_message.args_schema.model_fields
        assert "room_id" not in schema_fields
        assert "content" in schema_fields
        assert "mentions" in schema_fields

        # band_add_participant should have identifier and role, but NOT room_id
        add_participant = next(t for t in tools if t.name == "band_add_participant")
        schema_fields = add_participant.args_schema.model_fields
        assert "room_id" not in schema_fields
        assert "identifier" in schema_fields
        assert "role" in schema_fields

        # band_lookup_peers should expose pagination, but NOT room_id
        lookup_peers = next(t for t in tools if t.name == "band_lookup_peers")
        schema_fields = lookup_peers.args_schema.model_fields
        assert "room_id" not in schema_fields
        assert "page" in schema_fields
        assert "page_size" in schema_fields

    @pytest.mark.asyncio
    async def test_send_event_message_type_validation(
        self, CrewAIAdapter, crewai_mocks
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]

        send_event = next(t for t in tools if t.name == "band_send_event")
        schema_fields = send_event.args_schema.model_fields

        assert "message_type" in schema_fields
        # Required, not defaulted to "thought" — the master model is authoritative
        # and every other adapter already makes the agent state the event type.
        assert schema_fields["message_type"].is_required()

    def test_send_event_run_rejects_missing_message_type(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """A direct `_run` call must enforce the requiredness the schema enforces.

        crewai Flows call `_run` directly, bypassing args_schema validation, so
        without this an omitted message_type would silently post as "thought"
        again.
        """
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        send_event = next(t for t in tools if t.name == "band_send_event")

        with room_context("room-123"):
            result = send_event._run(content="no type given")

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "message_type" in result_data["message"]
        mock_tools.send_event.assert_not_called()

    def test_successful_tool_execution_with_room_context(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        """Tools work when context variable is set (simulates call during message handling)."""

        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        get_participants_tool = next(
            t for t in tools if t.name == "band_get_participants"
        )

        with room_context("room-123"):
            result = get_participants_tool._run()

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert "participants" in result_data
        assert result_data["count"] == 1

    def test_tool_execution_handles_exception(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        mock_tools.get_participants.side_effect = Exception("Connection failed")

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        get_participants_tool = next(
            t for t in tools if t.name == "band_get_participants"
        )

        with room_context("room-123"):
            result = get_participants_tool._run()

        result_data = json.loads(result)
        assert result_data["status"] == "error"
        assert "Connection failed" in result_data["message"]

    @pytest.mark.asyncio
    async def test_lookup_peers_uses_adapter_loop_when_tool_runs_in_worker_thread(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):
        crewai_mocks.Agent.reset_mock()

        adapter = CrewAIAdapter()
        await adapter.on_started("TestBot", "Test bot")

        expected_loop = asyncio.get_running_loop()

        async def lookup_peers(page: int, page_size: int) -> dict[str, object]:
            assert asyncio.get_running_loop() is expected_loop
            return {
                "peers": [],
                "metadata": {
                    "page": page,
                    "page_size": page_size,
                    "total_count": 0,
                    "total_pages": 1,
                },
            }

        mock_tools.lookup_peers = AsyncMock(side_effect=lookup_peers)

        call_kwargs = crewai_mocks.Agent.call_args[1]
        tools = call_kwargs["tools"]
        lookup_peers_tool = next(t for t in tools if t.name == "band_lookup_peers")

        with room_context("room-123"):
            result = await asyncio.to_thread(lookup_peers_tool._run)

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        mock_tools.lookup_peers.assert_awaited_once_with(1, 50)
