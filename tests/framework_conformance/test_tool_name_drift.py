"""Conformance tests that detect tool-name drift between the central registry and adapters.

When a new tool is added to ``TOOL_MODELS`` in ``runtime/tools.py``, these
tests will fail for any adapter or integration that is missing it — surfacing
the gap before it reaches production.

Strategy
--------
Each adapter / integration file is scanned for *string literals* that match
known tool names from the central ``TOOL_MODELS`` registry.  The found set is
compared against an *expected* set that describes which tools the file should
cover.

Files that derive their tool lists from the central constants (e.g. via
``mcp_tool_names()``) are verified to have the correct import, not rescanned
for individual names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from thenvoi.adapters.claude_sdk import _CLAUDE_SDK_AVAILABLE as _HAS_CLAUDE_SDK
from thenvoi.runtime.tools import (
    ALL_TOOL_NAMES,
    BASE_TOOL_NAMES,
    CHAT_TOOL_NAMES,
    CONTACT_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    iter_tool_definitions,
)

if _HAS_CLAUDE_SDK:
    from thenvoi.integrations.claude_sdk.tools import build_thenvoi_sdk_tools

_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "thenvoi"
if not _SRC_ROOT.is_dir():
    raise FileNotFoundError(f"Source root not found: {_SRC_ROOT}")


def _extract_tool_names(source: str) -> set[str]:
    """Extract all tool names from ``ALL_TOOL_NAMES`` referenced in source.

    Uses a trailing word-boundary regex to avoid false positives from
    prefix collisions (e.g. ``thenvoi_get_memory`` matching inside a
    hypothetical ``thenvoi_get_memory_context``).  The leading boundary is
    intentionally omitted so that MCP-prefixed names like
    ``mcp__thenvoi__thenvoi_send_message`` still match.

    Note: a tool name found anywhere in the source (including comments)
    counts as present.  This is a **first line of defence** that catches
    completely missing tools; actual ``@tool`` handler presence is verified
    by the per-adapter unit and integration tests.
    """
    return {
        name for name in ALL_TOOL_NAMES if re.search(re.escape(name) + r"\b", source)
    }


# ---------------------------------------------------------------------------
# Per-adapter / integration expected tool sets
# ---------------------------------------------------------------------------


class TestClaudeSDKAdapterToolDrift:
    """Claude SDK adapter (adapters/claude_sdk.py) must cover all tools."""

    _FILE = _SRC_ROOT / "adapters" / "claude_sdk.py"

    def test_derives_base_tools_from_central_registry(self):
        """Verify the adapter imports BASE_TOOL_NAMES instead of hardcoding."""
        source = self._FILE.read_text()
        assert "BASE_TOOL_NAMES" in source, (
            "Claude SDK adapter should import BASE_TOOL_NAMES from "
            "thenvoi.runtime.tools instead of hardcoding MCP tool names."
        )

    def test_derives_memory_tools_from_central_registry(self):
        """Verify the adapter imports MEMORY_TOOL_NAMES instead of hardcoding."""
        source = self._FILE.read_text()
        assert "MEMORY_TOOL_NAMES" in source, (
            "Claude SDK adapter should import MEMORY_TOOL_NAMES from "
            "thenvoi.runtime.tools instead of hardcoding MCP tool names."
        )

    @pytest.mark.skipif(
        not _HAS_CLAUDE_SDK,
        reason="claude-agent-sdk not installed (pip install thenvoi-sdk[claude_sdk])",
    )
    def test_shared_builder_covers_all_tools(self):
        """Every Thenvoi tool should be buildable for the Claude SDK adapter."""
        sdk_tools = build_thenvoi_sdk_tools(
            tool_definitions=iter_tool_definitions(include_memory=True),
            get_tools=lambda _room_id: None,
        )
        found = {tool.name for tool in sdk_tools}
        missing = ALL_TOOL_NAMES - found
        assert not missing, (
            f"Claude SDK adapter is missing tool wrappers for: {sorted(missing)}. "
            "Add the tool definition to the shared Claude SDK builder."
        )


class TestClaudeSDKIntegrationToolDrift:
    """Claude SDK integration (integrations/claude_sdk/tools.py) — chat tools only."""

    _FILE = _SRC_ROOT / "integrations" / "claude_sdk" / "tools.py"

    def test_derives_tool_list_from_central_registry(self):
        """Verify THENVOI_CHAT_TOOLS is derived from CHAT_TOOL_NAMES."""
        source = self._FILE.read_text()
        assert "CHAT_TOOL_NAMES" in source, (
            "Claude SDK integration tools should import CHAT_TOOL_NAMES from "
            "thenvoi.runtime.tools instead of hardcoding MCP tool names."
        )

    def test_delegates_to_shared_builder(self):
        """The integration should delegate tool wrapping to the shared Claude helper."""
        source = self._FILE.read_text()
        assert "build_thenvoi_sdk_tools(" in source
        assert "create_thenvoi_sdk_mcp_server(" in source


class TestClaudeSDKPromptsToolDrift:
    """Claude SDK prompts (integrations/claude_sdk/prompts.py) — chat tools only."""

    _FILE = _SRC_ROOT / "integrations" / "claude_sdk" / "prompts.py"

    def test_all_chat_tools_documented_in_prompt(self):
        """Every chat tool is mentioned in the system prompt template."""
        source = self._FILE.read_text()
        found = _extract_tool_names(source)
        missing = CHAT_TOOL_NAMES - found
        assert not missing, (
            f"Claude SDK prompts are missing documentation for: {sorted(missing)}. "
            f"Add tool documentation to the system prompt in prompts.py."
        )


class TestLangGraphToolDrift:
    """LangGraph integration (integrations/langgraph/langchain_tools.py)."""

    _FILE = _SRC_ROOT / "integrations" / "langgraph" / "langchain_tools.py"

    def test_all_tools_registered(self):
        """Every tool in TOOL_MODELS has a StructuredTool wrapper."""
        source = self._FILE.read_text()
        found = _extract_tool_names(source)
        missing = ALL_TOOL_NAMES - found
        assert not missing, (
            f"LangGraph integration is missing StructuredTool wrappers for: "
            f"{sorted(missing)}. Add wrappers in create_langchain_tools()."
        )


class TestCrewAIToolDrift:
    """CrewAI tool classes (integrations/crewai/tools.py).

    The BaseTool subclasses were moved out of ``adapters/crewai.py`` into the
    shared ``integrations/crewai/tools.py`` module so that both the legacy
    ``CrewAIAdapter`` and the new ``CrewAIFlowAdapter`` consume one set of
    wrappers. The drift check now scans the shared module.
    """

    _FILE = _SRC_ROOT / "integrations" / "crewai" / "tools.py"

    def test_all_tools_registered(self):
        """Every tool in TOOL_MODELS has a CrewAI tool class."""
        source = self._FILE.read_text()
        found = _extract_tool_names(source)
        missing = ALL_TOOL_NAMES - found
        assert not missing, (
            f"CrewAI adapter is missing tool classes for: {sorted(missing)}. "
            f"Add tool classes in _register_tools()."
        )


class TestPydanticAIToolDrift:
    """PydanticAI adapter (adapters/pydantic_ai.py)."""

    _FILE = _SRC_ROOT / "adapters" / "pydantic_ai.py"

    def test_all_tools_registered(self):
        """Every tool in TOOL_MODELS has a PydanticAI tool function."""
        source = self._FILE.read_text()
        found = _extract_tool_names(source)
        missing = ALL_TOOL_NAMES - found
        assert not missing, (
            f"PydanticAI adapter is missing tool functions for: {sorted(missing)}. "
            f"Add tool registrations in _register_tools()."
        )


class TestGeminiToolDrift:
    """Gemini adapter (adapters/gemini.py) must cover all tools.

    The Gemini adapter derives tool declarations dynamically from
    ``get_openai_tool_schemas()`` so individual tool names do not appear as
    string literals.  Instead we verify it uses the dynamic schema mechanism.
    """

    _FILE = _SRC_ROOT / "adapters" / "gemini.py"

    def test_derives_tools_from_openai_schemas(self):
        """Verify the adapter builds tools via get_openai_tool_schemas()."""
        source = self._FILE.read_text()
        assert "get_openai_tool_schemas" in source, (
            "Gemini adapter should use get_openai_tool_schemas() to derive "
            "tool declarations dynamically from the central registry."
        )

    def test_supports_memory_tools_toggle(self):
        """Verify include_memory is wired through to get_openai_tool_schemas."""
        source = self._FILE.read_text()
        assert "include_memory" in source, (
            "Gemini adapter should pass include_memory to "
            "get_openai_tool_schemas() so memory tools can be toggled."
        )


class TestParlantToolDrift:
    """Parlant integration (integrations/parlant/tools.py) — chat tools only."""

    _FILE = _SRC_ROOT / "integrations" / "parlant" / "tools.py"

    def test_all_chat_tools_registered(self):
        """Every chat tool has a Parlant tool function."""
        source = self._FILE.read_text()
        found = _extract_tool_names(source)
        missing = CHAT_TOOL_NAMES - found
        assert not missing, (
            f"Parlant integration is missing tool functions for: {sorted(missing)}. "
            f"Add tool implementations in create_parlant_tools()."
        )


# ---------------------------------------------------------------------------
# Cross-cutting sanity checks
# ---------------------------------------------------------------------------


class TestToolRegistryConsistency:
    """Verify the derived sets are consistent with TOOL_MODELS."""

    def test_all_equals_base_plus_memory(self):
        assert ALL_TOOL_NAMES == BASE_TOOL_NAMES | MEMORY_TOOL_NAMES

    def test_base_equals_chat_plus_contact(self):
        assert BASE_TOOL_NAMES == CHAT_TOOL_NAMES | CONTACT_TOOL_NAMES

    def test_no_overlap_chat_contact(self):
        assert not (CHAT_TOOL_NAMES & CONTACT_TOOL_NAMES)

    def test_no_overlap_base_memory(self):
        assert not (BASE_TOOL_NAMES & MEMORY_TOOL_NAMES)

    def test_memory_tools_subset_of_all(self):
        assert MEMORY_TOOL_NAMES <= ALL_TOOL_NAMES

    def test_contact_tools_subset_of_all(self):
        assert CONTACT_TOOL_NAMES <= ALL_TOOL_NAMES

    def test_all_memory_prefixed_tools_in_memory_set(self):
        """Catch new memory tools not added to MEMORY_TOOL_NAMES."""
        memory_like = {n for n in ALL_TOOL_NAMES if "memory" in n or "memories" in n}
        assert memory_like <= MEMORY_TOOL_NAMES, (
            f"Tools matching memory naming convention not in MEMORY_TOOL_NAMES: "
            f"{memory_like - MEMORY_TOOL_NAMES}"
        )

    def test_all_contact_prefixed_tools_in_contact_set(self):
        """Catch new contact tools not added to CONTACT_TOOL_NAMES."""
        contact_like = {n for n in ALL_TOOL_NAMES if "contact" in n}
        assert contact_like <= CONTACT_TOOL_NAMES, (
            f"Tools matching contact naming convention not in CONTACT_TOOL_NAMES: "
            f"{contact_like - CONTACT_TOOL_NAMES}"
        )
