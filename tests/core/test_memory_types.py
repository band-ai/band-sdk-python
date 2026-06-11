"""Tests for canonical memory enum types."""

from __future__ import annotations

from thenvoi.core.memory_types import (
    MEMORY_SYSTEM_TYPE_MAP,
    MemorySystem,
    SensoryMemoryType,
    WorkingLongTermMemoryType,
    enum_values,
)


class TestMemorySystemTypeMap:
    def test_keys_match_memory_system(self):
        assert set(MEMORY_SYSTEM_TYPE_MAP) == set(enum_values(MemorySystem))

    def test_mapped_types_cover_memory_type_union(self):
        mapped_types = {
            memory_type
            for types in MEMORY_SYSTEM_TYPE_MAP.values()
            for memory_type in types
        }
        sensory = set(enum_values(SensoryMemoryType))
        working_long_term = set(enum_values(WorkingLongTermMemoryType))
        assert mapped_types == sensory | working_long_term

    def test_sensory_and_working_long_term_types_are_disjoint(self):
        sensory = set(enum_values(SensoryMemoryType))
        working_long_term = set(enum_values(WorkingLongTermMemoryType))
        assert sensory.isdisjoint(working_long_term)

    def test_working_and_long_term_share_types(self):
        assert (
            MEMORY_SYSTEM_TYPE_MAP[MemorySystem.WORKING.value]
            == MEMORY_SYSTEM_TYPE_MAP[MemorySystem.LONG_TERM.value]
        )
