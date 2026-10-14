"""BuiltinToolRegistration behavior for Pydantic AI."""

from __future__ import annotations

import pytest

from band.core.types import ALL_CAPABILITIES, Capability
from band.runtime.tools import (
    ALL_TOOL_NAMES,
    CHAT_TOOL_NAMES,
    CONTACT_TOOL_NAMES,
    FILE_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    TASK_TOOL_NAMES,
    BandTool,
    ToolCategory,
)
from tests.adapters.pydanticai.kit import _registered_names, _started_adapter


class TestBuiltinToolRegistration:
    """Exactly which built-in tools a set of features puts on the agent.

    Expectations are the registry's own name sets, so a newly added tool is
    covered by whichever bucket it was declared in rather than by a literal
    list someone has to remember to extend.
    """

    @pytest.mark.asyncio
    async def test_default_features_register_only_chat_tools(self):
        adapter = await _started_adapter()

        assert _registered_names(adapter) == CHAT_TOOL_NAMES

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("capabilities", "expected"),
        [
            (Capability.CONTACTS, CHAT_TOOL_NAMES | CONTACT_TOOL_NAMES),
            (Capability.MEMORY, CHAT_TOOL_NAMES | MEMORY_TOOL_NAMES),
            (Capability.FILES, CHAT_TOOL_NAMES | FILE_TOOL_NAMES),
            (Capability.TASKS, CHAT_TOOL_NAMES | TASK_TOOL_NAMES),
            (ALL_CAPABILITIES, ALL_TOOL_NAMES),
        ],
        ids=["contacts", "memory", "files", "tasks", "all"],
    )
    async def test_capability_selects_exact_tool_set(self, capabilities, expected):
        adapter = await _started_adapter(capabilities=capabilities)

        assert _registered_names(adapter) == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("feature_filter", "expected"),
        [
            ({"include_tools": [BandTool.SEND_MESSAGE]}, {BandTool.SEND_MESSAGE}),
            (
                {"exclude_tools": [BandTool.SEND_EVENT]},
                ALL_TOOL_NAMES - {BandTool.SEND_EVENT},
            ),
            ({"include_categories": [ToolCategory.TASKS]}, TASK_TOOL_NAMES),
        ],
        ids=["include_tools", "exclude_tools", "include_categories"],
    )
    async def test_feature_filters_narrow_the_capability_set(
        self, feature_filter, expected
    ):
        adapter = await _started_adapter(
            capabilities=ALL_CAPABILITIES, **feature_filter
        )

        assert _registered_names(adapter) == expected

    @pytest.mark.asyncio
    async def test_filters_cannot_reintroduce_a_gated_tool(self):
        """A filter narrows what the capabilities allow; it never widens it."""
        adapter = await _started_adapter(include_tools=[BandTool.STORE_MEMORY])

        assert _registered_names(adapter) == set()
