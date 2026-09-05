"""Canonical task-board enum types shared across runtime and framework integrations."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, get_args


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


TaskIncludeOption = Literal["history"]
"""The single ``include`` value ``band_get_task``/``band_get_board`` accept.

The canonical type for ``include`` everywhere it appears -- GetTaskInput/
GetBoardInput's fields and AgentTools.get_task/get_board's parameter.
"""


def validate_include(include: str | None) -> None:
    """Raise ``ValueError`` unless *include* is ``None`` or a valid ``TaskIncludeOption``.

    Backstops GetTaskInput/GetBoardInput's typing for adapters (Parlant,
    pydantic-ai) that hand-register ``AgentTools.get_task``/``get_board`` as
    plain functions and never construct/validate the input model.
    """
    if include is not None and include not in get_args(TaskIncludeOption):
        raise ValueError(
            f"include must be {get_args(TaskIncludeOption)[0]!r} or omitted"
        )


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
