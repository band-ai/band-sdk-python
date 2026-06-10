"""Block Kit rendering for the tool-call progress UI.

When the inner brain emits ``tool_call`` / ``tool_result`` events
(gated on ``Emit.EXECUTION``), the SlackAdapter surfaces them as a
Block Kit "plan" rendered into the bound Slack thread. Tasks flip
state (``in_progress`` → ``completed``/``error``) as work progresses;
the same message is updated in place via ``chat.update``.

Slack doesn't expose a first-class ``plan`` block in stable Block Kit
yet, so we render with ordinary ``section`` + ``divider`` blocks —
this works in every workspace without depending on Agents & AI Apps
beta blocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    """Lifecycle of a task in the plan."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ERROR = "error"


_STATE_EMOJI: dict[TaskState, str] = {
    TaskState.PENDING: "⏸",
    TaskState.IN_PROGRESS: "⏳",
    TaskState.COMPLETED: "✅",
    TaskState.ERROR: "❌",
}

# Default Band platform tools considered "write" / mutating. The
# SlackAdapter user can override this via constructor arg.
DEFAULT_WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "band_send_message",
        "band_add_participant",
        "band_remove_participant",
        "band_create_chatroom",
        "band_store_memory",
        "band_supersede_memory",
        "band_archive_memory",
        "band_add_contact",
        "band_remove_contact",
        "band_respond_contact_request",
    }
)

# Fallback notification text when the message has only blocks. Slack
# uses this for desktop/mobile notifications and screen readers.
FALLBACK_TEXT_WORKING = "Working on it…"
FALLBACK_TEXT_DONE = "Done"

# Slack rejects a message with more than 50 blocks
# (https://docs.slack.dev/reference/block-kit/blocks/). render_plan_blocks
# emits header + optional divider + one section per task, so we cap the
# number of rendered task sections and replace the overflow with a single
# summary block ("…and N more").
MAX_BLOCKS = 50


@dataclass
class PlanTask:
    """One task in the plan. Linked to its triggering ``tool_call_id``."""

    id: str
    label: str
    state: TaskState = TaskState.PENDING
    is_write: bool = False
    error_message: str | None = None


@dataclass
class PlanState:
    """Mutable per-invocation plan state.

    Created lazily on first ``tool_call`` event; lives for the duration
    of one brain invocation. ``message_ts`` is populated after the first
    ``chat.postMessage`` so subsequent updates can target the same Slack
    message via ``chat.update``.
    """

    message_ts: str | None = None
    tasks: list[PlanTask] = field(default_factory=list)
    tasks_by_id: dict[str, PlanTask] = field(default_factory=dict)


def humanize_tool_name(name: str) -> str:
    """Turn a tool identifier into a human-readable label.

    Examples::

        humanize_tool_name("band_send_message") == "Send message"
        humanize_tool_name("band_lookup_peers") == "Lookup peers"
        humanize_tool_name("my_custom_tool") == "My custom tool"
    """
    stripped = name
    for prefix in ("band_", "slack_"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :]
            break
    return stripped.replace("_", " ").strip().capitalize() or name


def render_plan_blocks(plan: PlanState) -> list[dict[str, Any]]:
    """Build Slack Block Kit blocks from the current plan state."""
    has_tasks = bool(plan.tasks)
    all_terminal = has_tasks and all(
        t.state in (TaskState.COMPLETED, TaskState.ERROR) for t in plan.tasks
    )
    any_errors = any(t.state == TaskState.ERROR for t in plan.tasks)

    if not has_tasks:
        header = f"*🤖 {FALLBACK_TEXT_WORKING}*"
    elif all_terminal and any_errors:
        header = "*⚠️ Done with errors*"
    elif all_terminal:
        header = "*✅ Done*"
    else:
        header = f"*🤖 {FALLBACK_TEXT_WORKING}*"

    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
    ]
    if plan.tasks:
        blocks.append({"type": "divider"})

    # Reserve one block for an overflow summary when we can't fit every
    # task section under the 50-block ceiling. ``blocks`` already holds the
    # header (+ divider), so the remaining budget is what's left after that.
    overflow = len(plan.tasks) > (MAX_BLOCKS - len(blocks))
    task_budget = (MAX_BLOCKS - len(blocks) - 1) if overflow else len(plan.tasks)

    for task in plan.tasks[:task_budget]:
        emoji = _STATE_EMOJI[task.state]
        label = f"*{task.label}*" if task.is_write else task.label
        if task.is_write:
            label = f"✏️ {label}"
        text = f"{emoji} {label}"
        if task.state == TaskState.ERROR and task.error_message:
            # Truncate error messages so they don't blow up the block size.
            err = task.error_message.strip()
            if len(err) > 240:
                err = err[:237] + "…"
            text += f"\n_{err}_"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text}})

    if overflow:
        remaining = len(plan.tasks) - task_budget
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"_…and {remaining} more_"},
            }
        )

    return blocks


def plan_fallback_text(plan: PlanState) -> str:
    """Plain-text fallback for screen readers and notifications."""
    if not plan.tasks:
        return FALLBACK_TEXT_WORKING
    if all(t.state in (TaskState.COMPLETED, TaskState.ERROR) for t in plan.tasks):
        return FALLBACK_TEXT_DONE
    return FALLBACK_TEXT_WORKING
