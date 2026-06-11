"""Tests for canonical memory enum types."""

from __future__ import annotations

import pytest

from band.core.memory_types import (
    MEMORY_SYSTEM_TYPE_MAP,
    MemoryStoreScope,
    MemorySystem,
    SensoryMemoryType,
    WorkingLongTermMemoryType,
    enum_values,
    validate_subject_scope,
)


class TestMemorySystemTypeMap:
    def test_keys_match_memory_system(self):
        """Every memory system has an entry in the type map."""
        assert set(MEMORY_SYSTEM_TYPE_MAP) == set(enum_values(MemorySystem))

    def test_mapped_types_cover_memory_type_union(self):
        """The system map covers every supported memory type."""
        mapped_types = {
            memory_type
            for types in MEMORY_SYSTEM_TYPE_MAP.values()
            for memory_type in types
        }
        sensory = set(enum_values(SensoryMemoryType))
        working_long_term = set(enum_values(WorkingLongTermMemoryType))
        assert mapped_types == sensory | working_long_term

    def test_sensory_and_working_long_term_types_are_disjoint(self):
        """Sensory types do not overlap with working/long-term types."""
        sensory = set(enum_values(SensoryMemoryType))
        working_long_term = set(enum_values(WorkingLongTermMemoryType))
        assert sensory.isdisjoint(working_long_term)

    def test_working_and_long_term_share_types(self):
        """Working and long-term systems accept the same type values."""
        assert (
            MEMORY_SYSTEM_TYPE_MAP[MemorySystem.WORKING.value]
            == MEMORY_SYSTEM_TYPE_MAP[MemorySystem.LONG_TERM.value]
        )


class TestValidateSubjectScope:
    def test_allows_organization_scope_without_subject_id(self) -> None:
        """Organization-scoped memories do not need a subject ID."""
        validate_subject_scope(MemoryStoreScope.ORGANIZATION, None)

    def test_allows_subject_scope_with_subject_id(self) -> None:
        """Subject-scoped memories are valid when a subject ID is present."""
        validate_subject_scope(
            MemoryStoreScope.SUBJECT,
            "550e8400-e29b-41d4-a716-446655440000",
        )

    def test_rejects_subject_scope_without_subject_id(self) -> None:
        """Subject-scoped memories require a concrete subject ID."""
        with pytest.raises(ValueError, match="requires a subject_id"):
            validate_subject_scope(MemoryStoreScope.SUBJECT, None)
