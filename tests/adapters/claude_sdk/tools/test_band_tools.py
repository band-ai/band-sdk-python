from __future__ import annotations

from band.adapters.claude_sdk import (
    BAND_ALL_TOOLS,
    BAND_BASE_TOOLS,
    BAND_MEMORY_TOOLS,
    BAND_TASK_TOOLS,
)
from band.runtime.tools import ALL_TOOL_NAMES, FILE_TOOL_NAMES, mcp_tool_names


class TestBandTools:
    """Tests for Band tool names constants."""

    def test_band_base_tools_list(self):
        """Should define base platform tools (always included)."""
        expected = {
            "mcp__band__band_send_message",
            "mcp__band__band_send_event",
            "mcp__band__band_add_participant",
            "mcp__band__band_remove_participant",
            "mcp__band__band_get_participants",
            "mcp__band__band_lookup_peers",
            "mcp__band__band_create_chatroom",
            # Contact management tools
            "mcp__band__band_list_contacts",
            "mcp__band__band_add_contact",
            "mcp__band__band_remove_contact",
            "mcp__band__band_list_contact_requests",
            "mcp__band__band_respond_contact_request",
        }

        assert set(BAND_BASE_TOOLS) == expected
        assert len(BAND_BASE_TOOLS) == len(set(BAND_BASE_TOOLS)), (
            "duplicate entries in BAND_BASE_TOOLS"
        )

    def test_band_memory_tools_list(self):
        """Should define memory tools (enterprise only - opt-in)."""
        expected = {
            "mcp__band__band_list_memories",
            "mcp__band__band_store_memory",
            "mcp__band__band_get_memory",
            "mcp__band__band_supersede_memory",
            "mcp__band__band_archive_memory",
        }

        assert set(BAND_MEMORY_TOOLS) == expected
        assert len(BAND_MEMORY_TOOLS) == len(set(BAND_MEMORY_TOOLS)), (
            "duplicate entries in BAND_MEMORY_TOOLS"
        )

    def test_band_all_tools_combines_base_and_memory(self):
        """BAND_ALL_TOOLS should combine base, memory, file, and task tools
        without duplicates."""

        assert set(BAND_ALL_TOOLS) == (
            set(BAND_BASE_TOOLS)
            | set(BAND_MEMORY_TOOLS)
            | set(mcp_tool_names(FILE_TOOL_NAMES))
            | set(BAND_TASK_TOOLS)
        )
        assert len(BAND_ALL_TOOLS) == len(set(BAND_ALL_TOOLS)), "duplicate entries"
        assert set(BAND_ALL_TOOLS) == set(mcp_tool_names(ALL_TOOL_NAMES)), (
            "BAND_ALL_TOOLS content does not match mcp_tool_names(ALL_TOOL_NAMES) — "
            "a tool may have been dropped from the registry"
        )
