"""Task-board input models -- gated behind ``Capability.TASKS``.

See ``chat`` for the single-source-of-truth-for-schemas note that applies to
every input model in this package.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from band.core.task_types import TaskAssignmentStatus, TaskLifecycleState, TaskListState


class ListTasksInput(BaseModel):
    """List the shared tasks on this room's task board, ordered by number.

    Defaults to active tasks (the working board); use state to read
    cancelled/superseded/archived tasks or "all". Use this to see what work
    exists before creating a new task or picking one up.
    """

    state: TaskListState | None = Field(
        None, description="Lifecycle filter (default: active)"
    )
    cursor: str | None = Field(
        None, description="Opaque pagination cursor from a previous response"
    )
    limit: int | None = Field(
        None, description="Page size (default 50, max 100)", ge=1, le=100
    )


class CreateTaskInput(BaseModel):
    """Create a shared task on this room's task board.

    The server assigns the id and the board number ("#N"). Use
    supersedes_id when this task replaces an existing one -- the old task
    is preserved as an audit record and points at its replacement. You are
    NOT assigned automatically: report your own status via band_update_task
    to join the task.
    """

    subject: str = Field(..., description="What needs to be done")
    detail: str | None = Field(None, description="Longer description (optional)")
    supersedes_id: str | None = Field(
        None,
        description=(
            "UUID or board number of the active task this one replaces (optional)"
        ),
    )


class GetTaskInput(BaseModel):
    """Read one task by UUID or board number.

    Works for any lifecycle state -- cancelled/superseded/archived tasks
    stay readable as audit records.
    """

    id: str = Field(..., description="Task UUID or board number")
    include: Literal["history"] | None = Field(
        None, description="Set to 'history' to embed the recent event history"
    )


class UpdateTaskInput(BaseModel):
    """Update a task -- one operation, all fields optional, at least one required.

    Send status to report YOUR OWN progress (your first status write joins
    you to the task -- no separate assign step). Send active_form to show
    what you are doing right now. Send comment to leave a note for the
    others. Send subject/detail to edit the task itself. Send state to
    cancel ("cancelled"), tidy away ("archived"), or restore an archived
    task ("active"). Several agents can work the same task; each has its
    own status and active_form.
    """

    id: str = Field(..., description="Task UUID or board number")
    status: TaskAssignmentStatus | None = Field(
        None,
        description="YOUR work status on this task (first write joins you to it)",
    )
    active_form: str | None = Field(
        None,
        description="YOUR live 'doing X' sentence, shown on the board while you work",
    )
    comment: str | None = Field(
        None,
        description=(
            "Append a note for the other participants (kept in the task history)"
        ),
    )
    subject: str | None = Field(None, description="Edit the task subject")
    detail: str | None = Field(None, description="Edit the task detail")
    state: TaskLifecycleState | None = Field(
        None,
        description="Lifecycle: cancel, archive, or restore ('active' un-archives)",
    )

    @model_validator(mode="after")
    def validate_at_least_one_field(self) -> "UpdateTaskInput":
        if not any(
            (
                self.status,
                self.active_form,
                self.comment,
                self.subject,
                self.detail,
                self.state,
            )
        ):
            raise ValueError(
                "At least one of status, active_form, comment, subject, detail, "
                "or state must be set"
            )
        return self


class GetTaskHistoryInput(BaseModel):
    """The append-only history of one task -- every status change, lifecycle
    transition, comment, and edit, with actor and timestamp, oldest first.

    Cursor-paginated and works for any lifecycle state; this is the
    full-ledger read behind the capped include="history" embed on
    band_get_task.
    """

    id: str = Field(..., description="Task UUID or board number")
    cursor: str | None = Field(
        None, description="Opaque pagination cursor from a previous response"
    )
    limit: int | None = Field(
        None, description="Page size (default 50, max 100)", ge=1, le=100
    )


class GetBoardInput(BaseModel):
    """Read this room's goal (the team mission).

    Returns an empty default (goal_title null) when no goal has been set
    yet. Pass include="history" to embed the goal's audit trail.
    """

    include: Literal["history"] | None = Field(
        None, description="Set to 'history' to embed the goal-audit trail"
    )


class SetBoardInput(BaseModel):
    """Set or update this room's goal (upsert).

    Send goal_title and/or goal_summary; only the fields you send are
    changed. Every change is recorded in the goal-audit trail.
    """

    goal_title: str | None = Field(None, description="The room's mission title")
    goal_summary: str | None = Field(None, description="The mission paragraph")
