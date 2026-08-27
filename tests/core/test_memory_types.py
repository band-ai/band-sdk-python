"""Tests for canonical memory enum types."""

from __future__ import annotations

from enum import StrEnum

import band_sdk_core
import pytest

from band.core.memory_types import (
    MEMORY_SYSTEM_TYPE_MAP,
    ORGANIZATION_SCOPE_REJECTED_CODE,
    MemorySegment,
    MemoryStatus,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
    enum_values,
    is_organization_scope_rejection,
    organization_scope_rejected_message,
    validate_subject_scope,
)


class TestMemorySystemTypeMap:
    def test_keys_match_memory_system(self):
        """Every memory system has an entry in the type map."""
        assert set(MEMORY_SYSTEM_TYPE_MAP) == set(enum_values(MemorySystem))

    def test_mapped_types_cover_memory_type_enum(self):
        """The system map covers every supported memory type."""
        mapped_types = {
            memory_type
            for types in MEMORY_SYSTEM_TYPE_MAP.values()
            for memory_type in types
        }
        assert mapped_types == set(enum_values(MemoryType))

    def test_sensory_and_working_long_term_types_are_disjoint(self):
        """Sensory types do not overlap with working/long-term types."""
        sensory = set(MEMORY_SYSTEM_TYPE_MAP[MemorySystem.SENSORY.value])
        working_long_term = set(MEMORY_SYSTEM_TYPE_MAP[MemorySystem.WORKING.value])
        assert sensory.isdisjoint(working_long_term)

    def test_working_and_long_term_share_types(self):
        """Working and long-term systems accept the same type values."""
        assert (
            MEMORY_SYSTEM_TYPE_MAP[MemorySystem.WORKING.value]
            == MEMORY_SYSTEM_TYPE_MAP[MemorySystem.LONG_TERM.value]
        )


def _core_members(cls: type) -> dict[str, object]:
    """Discover a band_sdk_core PyO3 enum's variants dynamically -- no
    hardcoded name list, so this actually catches core adding a member the
    local mirror doesn't know about yet."""
    return {
        name: getattr(cls, name)
        for name in dir(cls)
        if isinstance(getattr(cls, name), cls)
    }


class TestBandSdkCoreParity:
    """Guard against the local StrEnum mirrors drifting from band_sdk_core's
    canonical taxonomy -- band_sdk_core's classes are opaque PyO3 types (not
    str subclasses, not Pydantic-schema-generatable), so they can't replace
    these mirrors directly; this test is what keeps them equivalent."""

    @pytest.mark.parametrize(
        ("local_enum", "core_enum"),
        [
            (MemorySystem, band_sdk_core.MemorySystem),
            (MemoryType, band_sdk_core.MemoryType),
            (MemorySegment, band_sdk_core.MemorySegment),
            (MemoryStatus, band_sdk_core.MemoryStatus),
        ],
    )
    def test_wire_values_match(
        self, local_enum: type[StrEnum], core_enum: type
    ) -> None:
        local_values = set(enum_values(local_enum))
        core_values = {str(member) for member in _core_members(core_enum).values()}
        assert local_values == core_values

    def test_every_core_system_type_pair_agrees_with_local_map(self) -> None:
        """Cross-product built from core's own members (not the local
        enum's), so this catches core adding an entirely new system/type
        this ticket's local mirror doesn't know about yet."""
        systems = _core_members(band_sdk_core.MemorySystem)
        types = _core_members(band_sdk_core.MemoryType)
        mismatches = []
        for system in systems.values():
            for memory_type in types.values():
                system_value = str(system)
                type_value = str(memory_type)
                try:
                    band_sdk_core.validate_memory_type_for_system(system, memory_type)
                    core_says_valid = True
                except ValueError:
                    core_says_valid = False

                local_says_valid = type_value in MEMORY_SYSTEM_TYPE_MAP.get(
                    system_value, ()
                )
                if core_says_valid != local_says_valid:
                    mismatches.append((system_value, type_value))

        assert not mismatches


class TestValidateMemoryTypeForSystem:
    """Direct tests against band_sdk_core.validate_memory_type_for_system
    itself -- call the real installed binding, not a mock."""

    def test_valid_combo_returns_none(self) -> None:
        assert (
            band_sdk_core.validate_memory_type_for_system("sensory", "iconic") is None
        )

    def test_mismatched_combo_raises_with_issues(self) -> None:
        with pytest.raises(
            ValueError, match="type `semantic` is not valid"
        ) as exc_info:
            band_sdk_core.validate_memory_type_for_system("sensory", "semantic")
        assert exc_info.value.issues == (
            (
                "type",
                "invalid_value",
                "type `semantic` is not valid for system `sensory`; "
                "expected one of: iconic, echoic, haptic",
            ),
        )

    def test_unknown_system_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown memory system `bogus`"):
            band_sdk_core.validate_memory_type_for_system("bogus", "iconic")

    def test_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown memory type `bogus`"):
            band_sdk_core.validate_memory_type_for_system("sensory", "bogus")


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
