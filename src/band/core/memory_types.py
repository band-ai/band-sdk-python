"""Canonical memory enum types shared across runtime and framework integrations."""

from __future__ import annotations

from enum import StrEnum


class MemorySystem(StrEnum):
    """Memory tier; constrains valid ``type`` values via
    ``band_sdk_core.validate_memory_type_for_system``."""

    SENSORY = "sensory"  # Brief sensory inputs (iconic/echoic/haptic)
    WORKING = "working"  # Short-term session context (episodic/semantic/procedural)
    LONG_TERM = (
        "long_term"  # Persistent cross-conversation memory (same types as working)
    )


class MemoryType(StrEnum):
    """Types passed as ``type`` on store/list; must match the chosen system."""

    ICONIC = "iconic"  # Visual input
    ECHOIC = "echoic"  # Auditory input
    HAPTIC = "haptic"  # Tactile input
    EPISODIC = "episodic"  # Events that occurred
    SEMANTIC = "semantic"  # Facts, preferences, learned knowledge
    PROCEDURAL = "procedural"  # How to perform tasks


class MemorySegment(StrEnum):
    """Logical subject category for a stored memory. Mirrors
    ``band_sdk_core.MemorySegment``, kept locally only because the core
    class isn't Pydantic-schema-generatable."""

    USER = "user"  # User preferences or profile info
    AGENT = "agent"  # Facts or events about agents/entities
    TOOL = "tool"  # Tool usage or task procedures
    GUIDELINE = "guideline"  # Behavioral rules or policies


class MemoryStoreScope(StrEnum):
    """Visibility scope for ``band_store_memory``."""

    AGENT = "agent"  # Private to this agent; no subject_id
    SUBJECT = "subject"  # About one person/agent; requires subject_id
    ORGANIZATION = "organization"  # Shared org-wide; requires the agent's owner to belong to an organization


class MemoryListScope(StrEnum):
    """Scope filter for ``band_list_memories``."""

    AGENT = "agent"  # Agent-private memories only
    SUBJECT = "subject"  # Subject-scoped memories only
    ORGANIZATION = "organization"  # Organization-scoped memories only
    ALL = "all"  # Every scope (no scope filter)


class MemoryStatus(StrEnum):
    """Lifecycle state; list filter and set by supersede/archive tools.
    Mirrors ``band_sdk_core.MemoryStatus``, kept locally only because the
    core class isn't Pydantic-schema-generatable."""

    ACTIVE = "active"  # Normal, visible memories
    SUPERSEDED = "superseded"  # Outdated; soft-deleted via band_supersede_memory
    ARCHIVED = "archived"  # Hidden but preserved via band_archive_memory
    ALL = "all"  # Any status (no filter)


def enum_values(enum_cls: type[StrEnum]) -> tuple[str, ...]:
    """Return the string values for a StrEnum class."""
    return tuple(member.value for member in enum_cls)


# Descriptive mirror only -- prompt text and field descriptions read this,
# but the actual validity rule is enforced by
# band_sdk_core.validate_memory_type_for_system. A drift-guard test keeps
# the two in sync.
_SENSORY_TYPES = (
    MemoryType.ICONIC.value,
    MemoryType.ECHOIC.value,
    MemoryType.HAPTIC.value,
)
_WORKING_LONG_TERM_TYPES = (
    MemoryType.EPISODIC.value,
    MemoryType.SEMANTIC.value,
    MemoryType.PROCEDURAL.value,
)

MEMORY_SYSTEM_TYPE_MAP: dict[str, tuple[str, ...]] = {
    MemorySystem.SENSORY.value: _SENSORY_TYPES,
    MemorySystem.WORKING.value: _WORKING_LONG_TERM_TYPES,
    MemorySystem.LONG_TERM.value: _WORKING_LONG_TERM_TYPES,
}


def validate_subject_scope(
    scope: MemoryStoreScope,
    subject_id: str | None,
) -> None:
    """Require subject_id when storing a subject-scoped memory."""
    if scope == MemoryStoreScope.SUBJECT and subject_id is None:
        raise ValueError(
            f'scope="{MemoryStoreScope.SUBJECT.value}" requires a subject_id (the '
            "UUID of the person or agent the memory is about). You did not "
            "provide one. If you do not have a concrete subject UUID, retry "
            f'with scope="{MemoryStoreScope.AGENT.value}" and omit subject_id. '
            "Do not invent a UUID."
        )


def memory_type_field_description() -> str:
    """Build the store_memory ``type`` field description from the system map."""
    grouped: dict[tuple[str, ...], list[str]] = {}
    for system in MemorySystem:
        types = MEMORY_SYSTEM_TYPE_MAP[system.value]
        grouped.setdefault(types, []).append(system.value)

    pairings = (
        f"{'|'.join(systems)}={'/'.join(types)}" for types, systems in grouped.items()
    )
    return "Memory type - must match the chosen system: " + ", ".join(pairings)


def _organization_scope_caveat(agent_value: str, organization_value: str) -> str:
    """Shared caveat for the store/list scope field descriptions below."""
    return (
        f'"{organization_value}" requires the agent\'s owner to belong to an '
        f'organization; "{agent_value}" (private to this agent) works regardless.'
    )


def memory_store_scope_field_description() -> str:
    """Build the store_memory ``scope`` field description from the enum."""
    return "Visibility scope. " + _organization_scope_caveat(
        MemoryStoreScope.AGENT.value, MemoryStoreScope.ORGANIZATION.value
    )


def memory_list_scope_field_description() -> str:
    """Build the list_memories ``scope`` field description from the enum."""
    return "Filter by scope. " + _organization_scope_caveat(
        MemoryListScope.AGENT.value, MemoryListScope.ORGANIZATION.value
    )


# Platform error code for a 422 on scope="organization" when the agent's
# owner belongs to no organization.
ORGANIZATION_SCOPE_REJECTED_CODE = "org_scope_requires_organization"


def is_organization_scope_rejection(error_body: object) -> bool:
    """True if a REST 422 body is the platform's org-scope-requires-organization rejection."""
    return (
        isinstance(error_body, dict)
        and isinstance(error_body.get("error"), dict)
        and error_body["error"].get("code") == ORGANIZATION_SCOPE_REJECTED_CODE
    )


def organization_scope_rejected_message(agent_value: str) -> str:
    """Actionable retry guidance for a 422 org-scope rejection."""
    return (
        f'scope="{MemoryStoreScope.ORGANIZATION.value}" was rejected: the '
        f"agent's owner does not belong to an organization. Retry with "
        f'scope="{agent_value}" instead.'
    )
