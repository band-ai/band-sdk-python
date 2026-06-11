"""Canonical memory enum types shared across runtime and framework integrations."""

from __future__ import annotations

from enum import StrEnum


class MemorySystem(StrEnum):
    SENSORY = "sensory"
    WORKING = "working"
    LONG_TERM = "long_term"


class SensoryMemoryType(StrEnum):
    ICONIC = "iconic"
    ECHOIC = "echoic"
    HAPTIC = "haptic"


class WorkingLongTermMemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"


MemoryType = SensoryMemoryType | WorkingLongTermMemoryType


class MemorySegment(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL = "tool"
    GUIDELINE = "guideline"


class MemoryStoreScope(StrEnum):
    SUBJECT = "subject"
    ORGANIZATION = "organization"


class MemoryListScope(StrEnum):
    SUBJECT = "subject"
    ORGANIZATION = "organization"
    ALL = "all"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    ALL = "all"


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
