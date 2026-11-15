"""Direct CrewAI tools reject invalid arguments with one failure report."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from band.core.memory_types import (
    MemorySegment,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
)
from band.core.types import Capability
from tests.crewaicontract import tool_result_data


class TestArgumentValidation:
    @pytest.mark.parametrize(
        "name,args,field",
        [
            ("band_get_memory", {"memory_id": ""}, "memory_id"),
            ("band_get_memory", {}, "memory_id"),
            ("band_read_room_file", {"file_id": "../files/"}, "file_id"),
            ("band_get_task", {"id": "1", "include": "invalid"}, "include"),
            ("band_get_task_history", {"id": "1", "limit": 0}, "limit"),
            ("band_update_task", {"id": "1"}, "At least one"),
            ("band_update_task", {"id": "1", "status": "invalid"}, "status"),
            ("band_list_memories", {"page_size": 51}, "page_size"),
            ("band_list_memories", {"type": "fact"}, "type"),
            ("band_store_memory", {"content": "test", "system": "invalid"}, "system"),
            (
                "band_store_memory",
                {
                    "content": [],
                    "system": MemorySystem.WORKING,
                    "type": MemoryType.SEMANTIC,
                    "segment": MemorySegment.USER,
                    "thought": "store",
                    "scope": MemoryStoreScope.AGENT,
                },
                "content",
            ),
        ],
    )
    def test_direct_crewai_rejects_invalid_arguments_with_one_report_pair(
        self, builder_mod: Any, name: str, args: dict[str, object], field: str
    ) -> None:
        tools = MagicMock()
        method = name.removeprefix("band_")
        setattr(tools, method, AsyncMock())
        context = builder_mod.CrewAIToolContext(room_id="room-1", tools=tools)
        reporter = MagicMock()
        reporter.report_call = AsyncMock()
        reporter.report_result = AsyncMock()
        built = builder_mod.build_band_crewai_tools(
            get_context=lambda: context,
            reporter=reporter,
            capabilities=frozenset(
                {Capability.MEMORY, Capability.FILES, Capability.TASKS}
            ),
        )
        result = tool_result_data(next(t for t in built if t.name == name)._run(**args))
        assert result["status"] == "error"
        assert field in result["message"]
        getattr(tools, method).assert_not_awaited()
        reporter.report_call.assert_awaited_once()
        reporter.report_result.assert_awaited_once()
        assert reporter.report_result.call_args.kwargs["is_error"] is True
