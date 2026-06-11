"""Canonical memory enum types shared across runtime and framework integrations."""

from __future__ import annotations

from enum import StrEnum


class MemorySystem(StrEnum):
    """Memory tier; constrains valid ``type`` values via MEMORY_SYSTEM_TYPE_MAP."""

    SENSORY = "sensory"  # Brief sensory inputs (iconic/echoic/haptic)
    WORKING = "working"  # Short-term session context (episodic/semantic/procedural)
    LONG_TERM = (
        "long_term"  # Persistent cross-conversation memory (same types as working)
    )


class SensoryMemoryType(StrEnum):
    """Types allowed when ``system`` is sensory."""

    ICONIC = "iconic"  # Visual input
    ECHOIC = "echoic"  # Auditory input
    HAPTIC = "haptic"  # Tactile input


class WorkingLongTermMemoryType(StrEnum):
    """Types allowed when ``system`` is working or long_term."""

    EPISODIC = "episodic"  # Events that occurred
    SEMANTIC = "semantic"  # Facts, preferences, learned knowledge
    PROCEDURAL = "procedural"  # How to perform tasks


# Union passed as ``type`` on store/list; must match the chosen system.
MemoryType = SensoryMemoryType | WorkingLongTermMemoryType


class MemorySegment(StrEnum):
    """Logical subject category for a stored memory."""

    USER = "user"  # User preferences or profile info
    AGENT = "agent"  # Facts or events about agents/entities
    TOOL = "tool"  # Tool usage or task procedures
    GUIDELINE = "guideline"  # Behavioral rules or policies


class MemoryStoreScope(StrEnum):
    """Visibility scope for ``band_store_memory``."""

    SUBJECT = "subject"  # About one person/agent; requires subject_id
    ORGANIZATION = "organization"  # Shared org-wide; default when storing


class MemoryListScope(StrEnum):
    """Scope filter for ``band_list_memories``."""

    SUBJECT = "subject"  # Subject-scoped memories only
    ORGANIZATION = "organization"  # Organization-scoped memories only
    ALL = "all"  # Both scopes (no scope filter)


class MemoryStatus(StrEnum):
    """Lifecycle state; list filter and set by supersede/archive tools."""

    ACTIVE = "active"  # Normal, visible memories
    SUPERSEDED = "superseded"  # Outdated; soft-deleted via band_supersede_memory
    ARCHIVED = "archived"  # Hidden but preserved via band_archive_memory
    ALL = "all"  # Any status (no filter)


def enum_values(enum_cls: type[StrEnum]) -> tuple[str, ...]:
    """Return the string values for a StrEnum class."""
    return tuple(member.value for member in enum_cls)


MEMORY_SYSTEM_TYPE_MAP: dict[str, tuple[str, ...]] = {
    MemorySystem.SENSORY.value: enum_values(SensoryMemoryType),
    MemorySystem.WORKING.value: enum_values(WorkingLongTermMemoryType),
    MemorySystem.LONG_TERM.value: enum_values(WorkingLongTermMemoryType),
}


def validate_subject_scope(
    scope: MemoryStoreScope,
    subject_id: str | None,
) -> None:
    """Require subject_id when storing a subject-scoped memory.

    A subject-scoped memory without subject_id is silently unretrievable: list
    queries cannot match a null subject, and organization-wide results exclude
    subject-scoped rows. Callers should surface this as a tool validation error
    so the model can retry with scope="organization" or a real UUID.
    """
    if scope == MemoryStoreScope.SUBJECT and subject_id is None:
        raise ValueError(
            'scope="subject" requires a subject_id (the UUID of the person or '
            "agent the memory is about). You did not provide one. If you do not "
            'have a concrete subject UUID, retry with scope="organization" and '
            "omit subject_id. Do not invent a UUID."
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
