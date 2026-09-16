"""Memory-tool input models -- gated behind ``Capability.MEMORY``.

See ``chat`` for the single-source-of-truth-for-schemas note that applies to
every input model in this package.
"""

from __future__ import annotations

from typing import Any

import band_sdk_core
from pydantic import BaseModel, Field, model_validator

from band.core.memory_types import (
    MemoryListScope,
    MemorySegment,
    MemoryStatus,
    MemoryStoreScope,
    MemorySystem,
    MemoryType,
    memory_list_scope_field_description,
    memory_store_scope_field_description,
    memory_type_field_description,
    validate_subject_scope,
)


class ListMemoriesInput(BaseModel):
    """List memories accessible to the agent.

    Returns this agent's own private memories, memories about the specified
    subject (cross-agent sharing), and organization-wide shared memories.
    """

    subject_id: str | None = Field(
        None, description="Filter by subject UUID (required for subject-scoped queries)"
    )
    scope: MemoryListScope | None = Field(
        None, description=memory_list_scope_field_description()
    )
    system: MemorySystem | None = Field(None, description="Filter by memory system")
    type: MemoryType | None = Field(None, description="Filter by memory type")
    segment: MemorySegment | None = Field(None, description="Filter by segment")
    content_query: str | None = Field(None, description="Full-text search query")
    page_size: int = Field(50, description="Number of results per page", ge=1, le=50)
    status: MemoryStatus | None = Field(None, description="Filter by status")


class StoreMemoryInput(BaseModel):
    """Store a new memory entry.

    The memory will be associated with the authenticated agent as the source.
    For agent-scoped memories (private to this agent), omit subject_id.
    For subject-scoped memories, provide a subject_id.
    For organization-scoped memories, omit subject_id; this requires the
    agent's owner to belong to an organization.
    """

    content: str = Field(..., description="The memory content")
    system: MemorySystem = Field(..., description="Memory system tier")
    type: MemoryType = Field(..., description=memory_type_field_description())
    segment: MemorySegment = Field(..., description="Logical segment")
    thought: str = Field(..., description="Agent's reasoning for storing this memory")
    scope: MemoryStoreScope = Field(
        ..., description=memory_store_scope_field_description()
    )
    subject_id: str | None = Field(
        None,
        description="UUID of the subject this memory is about (required for subject scope)",
    )
    metadata: dict[str, Any] | None = Field(
        None, description="Additional metadata (tags, references)"
    )

    @model_validator(mode="after")
    def validate_memory_fields(self) -> "StoreMemoryInput":
        band_sdk_core.validate_memory_type_for_system(self.system, self.type)
        validate_subject_scope(self.scope, self.subject_id)
        return self


class GetMemoryInput(BaseModel):
    """Retrieve a specific memory by ID."""

    memory_id: str = Field(..., description="Memory ID (UUID)")


class SupersedeMemoryInput(BaseModel):
    """Mark a memory as superseded (soft delete).

    Use when information is outdated or incorrect.
    The memory remains for audit trail but won't appear in normal queries.
    Only the source agent can supersede.
    """

    memory_id: str = Field(..., description="Memory ID (UUID)")


class ArchiveMemoryInput(BaseModel):
    """Archive a memory (hide but preserve).

    Use when memory is valid but not currently needed.
    Archived memories can be restored later by humans.
    Only the source agent can archive.
    """

    memory_id: str = Field(..., description="Memory ID (UUID)")
