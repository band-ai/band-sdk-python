from __future__ import annotations

import asyncio
import json

from band.core.memory_types import MemoryType
from band.core.types import Capability


class TestMemoryToolExecution:
    def test_list_memories_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = CrewAIAdapter(capabilities=Capability.MEMORY)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        list_memories_tool = next(t for t in tools if t.name == "band_list_memories")

        with room_context("room-123"):
            result = list_memories_tool._run(
                subject_id="subject-1",
                scope="subject",
                system="working",
                type=MemoryType.SEMANTIC,
                segment="user",
                content_query="remember",
                page_size=5,
                status="active",
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["data"][0]["id"] == "memory-1"
        mock_tools.list_memories.assert_awaited_once_with(
            subject_id="subject-1",
            scope="subject",
            system="working",
            type=MemoryType.SEMANTIC,
            segment="user",
            content_query="remember",
            page_size=5,
            status="active",
        )

    def test_store_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = CrewAIAdapter(capabilities=Capability.MEMORY)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        store_memory_tool = next(t for t in tools if t.name == "band_store_memory")

        with room_context("room-123"):
            result = store_memory_tool._run(
                content="remember this",
                system="working",
                type=MemoryType.SEMANTIC,
                segment="user",
                thought="important for follow-up",
                scope="subject",
                subject_id="subject-1",
            )

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "stored"
        assert result_data["id"] == "memory-1"
        mock_tools.store_memory.assert_awaited_once_with(
            content="remember this",
            system="working",
            type=MemoryType.SEMANTIC,
            segment="user",
            thought="important for follow-up",
            scope="subject",
            subject_id="subject-1",
        )

    def test_get_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = CrewAIAdapter(capabilities=Capability.MEMORY)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        get_memory_tool = next(t for t in tools if t.name == "band_get_memory")

        with room_context("room-123"):
            result = get_memory_tool._run(memory_id="memory-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["id"] == "memory-1"
        mock_tools.get_memory.assert_awaited_once_with("memory-1")

    def test_supersede_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = CrewAIAdapter(capabilities=Capability.MEMORY)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        supersede_memory_tool = next(
            t for t in tools if t.name == "band_supersede_memory"
        )

        with room_context("room-123"):
            result = supersede_memory_tool._run(memory_id="memory-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "superseded"
        assert result_data["id"] == "memory-1"
        mock_tools.supersede_memory.assert_awaited_once_with("memory-1")

    def test_archive_memory_tool_executes(
        self, CrewAIAdapter, crewai_mocks, mock_tools, room_context
    ):

        adapter = CrewAIAdapter(capabilities=Capability.MEMORY)
        asyncio.run(adapter.on_started("TestBot", "Test bot"))

        tools = crewai_mocks.Agent.call_args[1]["tools"]
        archive_memory_tool = next(t for t in tools if t.name == "band_archive_memory")

        with room_context("room-123"):
            result = archive_memory_tool._run(memory_id="memory-1")

        result_data = json.loads(result)
        assert result_data["status"] == "success"
        assert result_data["result_status"] == "archived"
        assert result_data["id"] == "memory-1"
        mock_tools.archive_memory.assert_awaited_once_with("memory-1")
