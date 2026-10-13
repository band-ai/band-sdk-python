"""Contact tools follow the requested capabilities in tool definitions."""

from __future__ import annotations

from band.core.types import Capability
from band.runtime.tools import (
    CONTACT_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    iter_tool_definitions,
)


class TestIterToolDefinitionsContactsFlag:
    def test_contacts_included_by_default(self) -> None:
        defs = iter_tool_definitions()
        names = {d.name for d in defs}
        assert CONTACT_TOOL_NAMES.issubset(names)

    def test_contacts_excluded_when_flag_false(self) -> None:
        defs = iter_tool_definitions(capabilities=frozenset())
        names = {d.name for d in defs}
        assert names.isdisjoint(CONTACT_TOOL_NAMES)

    def test_memory_and_contacts_independent(self) -> None:
        defs = iter_tool_definitions(capabilities=frozenset({Capability.MEMORY}))
        names = {d.name for d in defs}
        assert names.isdisjoint(CONTACT_TOOL_NAMES)
        # Memory tools present
        assert MEMORY_TOOL_NAMES.issubset(names)
