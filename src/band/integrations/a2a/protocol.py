"""Small protocol helpers shared by the A2A integrations."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)

TERMINAL_TASK_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    }
)


def text_from_message(message: Message | None) -> str:
    """Return the text parts from a protobuf A2A message."""
    if message is None:
        return ""
    return "\n".join(part.text for part in message.parts if part.text)


def text_message(
    content: str,
    *,
    role: Role = Role.ROLE_AGENT,
    context_id: str | None = None,
    task_id: str | None = None,
) -> Message:
    """Build a text-only protobuf A2A message."""
    message = Message(
        message_id=str(uuid4()),
        role=role,
        parts=[Part(text=content)],
    )
    if context_id:
        message.context_id = context_id
    if task_id:
        message.task_id = task_id
    return message


def new_task(request: SendMessageRequest | Message) -> Task:
    """Create a working task from an incoming A2A message."""
    message = request.message if isinstance(request, SendMessageRequest) else request
    return Task(
        id=message.task_id or str(uuid4()),
        context_id=message.context_id or str(uuid4()),
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


def snapshot_task(task: Task) -> Task:
    """Return an independent task snapshot for event queue publication."""
    return deepcopy(task)


def apply_task_stream_event(task: Task | None, event: StreamResponse) -> Task | None:
    """Apply one task-bearing stream event and return the current task state."""
    if event.HasField("task"):
        return snapshot_task(event.task)

    if event.HasField("status_update"):
        update = event.status_update
        task = task or Task(id=update.task_id, context_id=update.context_id)
        task.status.CopyFrom(update.status)
        return task

    if event.HasField("artifact_update"):
        update = event.artifact_update
        task = task or Task(id=update.task_id, context_id=update.context_id)
        existing_artifact = next(
            (
                artifact
                for artifact in task.artifacts
                if artifact.artifact_id == update.artifact.artifact_id
            ),
            None,
        )
        if existing_artifact is None:
            task.artifacts.add().CopyFrom(update.artifact)
        elif update.append:
            existing_artifact.parts.extend(update.artifact.parts)
        else:
            existing_artifact.CopyFrom(update.artifact)
        return task

    return None


def task_id_from_stream_event(event: StreamResponse) -> str | None:
    """Return the task ID carried by a task-related stream event."""
    if event.HasField("task"):
        return event.task.id
    if event.HasField("status_update"):
        return event.status_update.task_id
    if event.HasField("artifact_update"):
        return event.artifact_update.task_id
    return None


def task_response_text(task: Task | None) -> str:
    """Extract a task's best available text response."""
    if task is None:
        return ""

    for artifact in task.artifacts:
        text = "\n".join(part.text for part in artifact.parts if part.text)
        if text:
            return text

    if task.status.message:
        text = text_from_message(task.status.message)
        if text:
            return text

    for message in reversed(task.history):
        if message.role == Role.ROLE_AGENT:
            text = text_from_message(message)
            if text:
                return text

    return ""


def is_terminal_state(state: int) -> bool:
    """Return whether an A2A task state ends execution."""
    return state in TERMINAL_TASK_STATES


def state_name(state: int) -> str:
    """Return the stable enum name for a protobuf task state."""
    return TaskState.Name(state)
