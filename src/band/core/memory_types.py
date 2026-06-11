"""Canonical memory enum types shared across runtime and framework integrations."""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

MemorySystem = Literal["sensory", "working", "long_term"]
SensoryMemoryType = Literal["iconic", "echoic", "haptic"]
WorkingLongTermMemoryType = Literal["episodic", "semantic", "procedural"]
MemoryType = Literal["iconic", "echoic", "haptic", "episodic", "semantic", "procedural"]
MemorySegment = Literal["user", "agent", "tool", "guideline"]
MemoryStoreScope = Literal["subject", "organization"]
MemoryListScope = Literal["subject", "organization", "all"]
MemoryStatus = Literal["active", "superseded", "archived", "all"]


def literal_values(annotation: Any) -> tuple[str, ...]:
    """Extract string Literal values, unwrapping ``X | None`` unions."""
    origin = get_origin(annotation)
    if origin is not None:
        args = get_args(annotation)
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return literal_values(non_none[0])
    return get_args(annotation)


def memory_system_type_map() -> dict[str, tuple[str, ...]]:
    """Map each memory system to its valid type values."""
    sensory_types = literal_values(SensoryMemoryType)
    working_long_term_types = literal_values(WorkingLongTermMemoryType)
    return {
        "sensory": sensory_types,
        "working": working_long_term_types,
        "long_term": working_long_term_types,
    }
