"""Tests for canonical memory enum types."""

from __future__ import annotations

from typing import get_args

from thenvoi.core.memory_types import (
    MemorySystem,
    MemoryType,
    SensoryMemoryType,
    WorkingLongTermMemoryType,
    literal_values,
    memory_system_type_map,
)


class TestMemorySystemTypeMap:
    def test_keys_match_memory_system(self):
        assert set(memory_system_type_map()) == set(get_args(MemorySystem))

    def test_mapped_types_cover_memory_type_union(self):
        mapped_types = {
            memory_type
            for types in memory_system_type_map().values()
            for memory_type in types
        }
        assert mapped_types == set(get_args(MemoryType))

    def test_sensory_and_working_long_term_types_are_disjoint(self):
        sensory = set(literal_values(SensoryMemoryType))
        working_long_term = set(literal_values(WorkingLongTermMemoryType))
        assert sensory.isdisjoint(working_long_term)

    def test_working_and_long_term_share_types(self):
        system_map = memory_system_type_map()
        assert system_map["working"] == system_map["long_term"]
