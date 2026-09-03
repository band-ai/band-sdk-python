"""Canonical task-board enum types shared across runtime and framework integrations."""

from __future__ import annotations

from enum import StrEnum


class TaskListState(StrEnum):
    """Filter value for ``band_list_tasks``. Mirrors
    ``band_rest.types.ListChatTasksRequestState``."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    ALL = "all"


class TaskLifecycleState(StrEnum):
    """Task-level lifecycle state set via ``band_update_task``'s ``state``
    argument. Mirrors ``band_rest.types.UpdateChatTaskRequestState``; setting
    it back to ``ACTIVE`` is how a cancelled/archived task is restored."""

    ACTIVE = "active"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class TaskAssignmentStatus(StrEnum):
    """Per-assignee progress status set via ``band_update_task``'s ``status``
    argument. Mirrors ``band_rest.types.UpdateChatTaskRequestStatus`` /
    ``band_rest.types.TaskAssignmentStatus``."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    IN_REVIEW = "in_review"
    FAILED = "failed"
    COMPLETED = "completed"
