"""Tests for canonical memory enum types."""

from __future__ import annotations

import pytest

from band.core.memory_types import (
    MEMORY_SYSTEM_TYPE_MAP,
    ORGANIZATION_SCOPE_REJECTED_CODE,
    MemoryStoreScope,
    MemorySystem,
    SensoryMemoryType,
    WorkingLongTermMemoryType,
    enum_values,
    is_organization_scope_rejection,
    organization_scope_rejected_message,
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

    def test_allows_agent_scope_without_subject_id(self) -> None:
        """Agent-scoped memories do not need a subject ID."""
        validate_subject_scope(MemoryStoreScope.AGENT, None)

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

    def test_no_subject_id_error_recommends_agent_scope(self) -> None:
        """The fallback must be `scope="agent"`, not `scope="organization"` --
        organization scope fails for an agent whose owner has no organization,
        so pointing an LLM there on retry just guarantees the next call fails too."""
        with pytest.raises(ValueError, match='scope="agent"'):
            validate_subject_scope(MemoryStoreScope.SUBJECT, None)


class TestIsOrganizationScopeRejection:
    def test_true_for_the_platform_error_shape(self) -> None:
        """The exact shape the platform sends for a 422 org-scope rejection."""
        body = {
            "error": {
                "code": ORGANIZATION_SCOPE_REJECTED_CODE,
                "details": {"organization_id": "must be present"},
            }
        }
        assert is_organization_scope_rejection(body) is True

    def test_false_for_a_different_error_code(self) -> None:
        """A 422 for an unrelated reason must not be misreported as scope rejection."""
        body = {"error": {"code": "some_other_validation_failure"}}
        assert is_organization_scope_rejection(body) is False

    def test_false_for_non_dict_bodies(self) -> None:
        """A REST error body isn't guaranteed to be a dict; must not raise."""
        assert is_organization_scope_rejection(None) is False
        assert is_organization_scope_rejection("plain text error") is False
        assert is_organization_scope_rejection({"error": "not a dict"}) is False


class TestOrganizationScopeRejectedMessage:
    def test_recommends_the_given_agent_value(self) -> None:
        """Message must name the caller's own agent-scope value (store vs.
        list use different enums that happen to share the same value)."""
        message = organization_scope_rejected_message(MemoryStoreScope.AGENT.value)
        assert 'scope="agent"' in message
        assert 'scope="organization"' in message
