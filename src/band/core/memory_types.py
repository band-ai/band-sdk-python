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
