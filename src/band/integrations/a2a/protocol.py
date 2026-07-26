"""Small protocol helpers shared by the A2A integrations."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from a2a.types import (
    Message,
    Part,
    Role,
    SendMessageRequest,
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


def is_terminal_state(state: int) -> bool:
    """Return whether an A2A task state ends execution."""
    return state in TERMINAL_TASK_STATES


def state_name(state: int) -> str:
    """Return the stable enum name for a protobuf task state."""
    return TaskState.Name(state)
