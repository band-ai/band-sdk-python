"""How a Codex turn's activity reads in the room: tool calls, thoughts, tasks.

The adapter decides which of these its ``emit`` features and config allow;
these functions only render and send them.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from band.adapters.codex.items import (
    THOUGHT_ITEM_TYPES,
    TOOL_ITEM_TYPES,
    UNRENDERED_ITEM_TYPES,
    item_call_id,
    thought_text,
    tool_item,
)
from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import Emit, ToolEventKey, TurnUsage
from band.integrations.codex.types import (
    ApprovalAuditEntry,
    CodexMetadataKey,
    CodexTokenUsage,
    build_agent_failure,
    parse_plan_steps,
)

logger = logging.getLogger(__name__)

# Bounds the titles remembered between task_started and task_complete.
MAX_TASK_TITLES = 500

# Cap on the raw diff forwarded in turn/diff/updated metadata, in UTF-8 bytes
# so multi-byte content cannot inflate the frame past it. Consumers that need
# the full diff ask Codex for it.
MAX_DIFF_METADATA_BYTES = 64 * 1024

RAW_TASK_STARTED = "codex/event/task_started"
RAW_TASK_METHODS = frozenset({RAW_TASK_STARTED, "codex/event/task_complete"})


@dataclass(frozen=True)
class TurnRef:
    """The room, thread and native turn an event belongs to."""

    room_id: str
    thread_id: str
    turn_id: str | None

    def metadata(self) -> dict[str, Any]:
        return {
            CodexMetadataKey.ROOM: self.room_id,
            CodexMetadataKey.THREAD: self.thread_id,
            CodexMetadataKey.TURN: self.turn_id,
        }


def task_event_content(
    *, task_id: str | None, task: str, status: str, summary: str | None = None
) -> str:
    lines = [f"UUID: {task_id}"] if task_id else []
    lines += [f"Task: {task}", f"Status: {status}"]
    if summary and summary != task:
        lines.append(f"Summary: {summary}")
    return "\n".join(lines)


def _task_field(
    params: dict[str, Any],
    keys: tuple[str, ...],
    nested_keys: tuple[str, ...],
    *,
    task_text: bool = False,
) -> str | None:
    """The first non-empty string under ``keys``, then under ``task``."""
    for key in keys:
        if isinstance(value := params.get(key), str) and value:
            return value
    task = params.get("task")
    if task_text and isinstance(task, str) and task:
        return task
    if isinstance(task, dict):
        for key in nested_keys:
            if isinstance(value := task.get(key), str) and value:
                return value
    return None


_SUMMARY_KEYS = ("summary", "result", "message", "description")


def task_event_id(params: dict[str, Any]) -> str | None:
    return _task_field(params, ("taskId", "task_id"), ("taskId", "task_id", "id"))


def task_event_title(params: dict[str, Any]) -> str | None:
    return _task_field(
        params, ("title", "name"), ("title", "name", "description"), task_text=True
    )


def task_event_summary(params: dict[str, Any]) -> str | None:
    return _task_field(params, _SUMMARY_KEYS, _SUMMARY_KEYS)


class TaskTitles:
    """Each room's task titles, from task_started until task_complete."""

    def __init__(self, limit: int = MAX_TASK_TITLES) -> None:
        self._limit = limit
        self._rooms: dict[str, OrderedDict[str, str]] = {}

    def remember(self, room_id: str, task_id: str, title: str) -> None:
        titles = self._rooms.setdefault(room_id, OrderedDict())
        titles[task_id] = title
        if len(titles) > self._limit:
            titles.popitem(last=False)

    def recall(self, room_id: str, task_id: str) -> str | None:
        return self._rooms.get(room_id, {}).get(task_id)

    def forget(self, room_id: str, task_id: str) -> None:
        self._rooms.get(room_id, {}).pop(task_id, None)

    def forget_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)


def turn_usage(usage: CodexTokenUsage | None) -> TurnUsage:
    """This turn's token usage, from Codex's per-turn deltas.

    Codex reports no cache dimension, and counts reasoning disjointly from
    output (its total is ``input + output + reasoning``), so reasoning folds
    into ``output_tokens`` as other providers count it.
    """
    return TurnUsage.from_object(
        usage,
        input="turn_input_tokens",
        output="turn_output_tokens",
        reasoning="turn_reasoning_tokens",
    )


def turn_error_message(turn_payload: dict[str, Any]) -> str:
    error = turn_payload.get("error")
    if isinstance(error, dict) and isinstance(message := error.get("message"), str):
        return message
    return ""


async def item_completed(
    tools: AgentToolsProtocol,
    ref: TurnRef,
    item: dict[str, Any],
    emit: frozenset[Emit],
) -> None:
    """Show a completed item's tool call or thought, as ``emit`` allows.

    Best-effort: a failure is logged, never raised, so supplementary
    reporting cannot end the turn.
    """
    item_type = item.get("type", "")
    item_id = item_call_id(item)
    try:
        await _show_item(tools, ref, item, item_type, item_id, emit)
    except Exception:
        logger.debug(
            "Failed to emit %s event for item %s (best-effort)",
            item_type,
            item_id,
            exc_info=True,
        )


async def _show_item(
    tools: AgentToolsProtocol,
    ref: TurnRef,
    item: dict[str, Any],
    item_type: str,
    item_id: str,
    emit: frozenset[Emit],
) -> None:
    metadata = ref.metadata()
    if item_type in TOOL_ITEM_TYPES:
        if Emit.TOOL_CALLS not in emit:
            return
        name, args, output = tool_item(item_type, item)
        for message_type, key, value in (
            ("tool_call", ToolEventKey.ARGS, args),
            ("tool_result", ToolEventKey.OUTPUT, output),
        ):
            content = {
                ToolEventKey.NAME: name,
                key: value,
                ToolEventKey.TOOL_CALL_ID: item_id,
            }
            await tools.send_event(
                content=json.dumps(content),
                message_type=message_type,
                metadata=metadata,
            )
    elif item_type in THOUGHT_ITEM_TYPES:
        # An empty reasoning or plan item says nothing, so it is not shown.
        if Emit.THOUGHTS in emit and (text := thought_text(item_type, item)):
            await tools.send_event(
                content=text, message_type="thought", metadata=metadata
            )
    elif item_type not in UNRENDERED_ITEM_TYPES:
        logger.debug("Unhandled item/completed type: %s", item_type)


async def raw_task_event(
    tools: AgentToolsProtocol,
    ref: TurnRef,
    method: str,
    params: dict[str, Any],
    titles: TaskTitles,
) -> None:
    """Forward a raw task lifecycle event, titled from its start."""
    is_started = method == RAW_TASK_STARTED
    task_phase = "started" if is_started else "completed"
    task_id = task_event_id(params)
    title = task_event_title(params)
    if task_id and title and is_started:
        titles.remember(ref.room_id, task_id, title)
    if task_id and not title:
        title = titles.recall(ref.room_id, task_id)
    summary = task_event_summary(params)
    if not title:
        title = "Codex task lifecycle event"
        summary = summary or f"Method: {method}"

    metadata = ref.metadata() | {
        "codex_event_method": method,
        "codex_task_phase": task_phase,
    }
    if task_id:
        metadata["codex_task_id"] = task_id
    if params:
        metadata["codex_event_params"] = params
    await tools.send_event(
        content=task_event_content(
            task_id=task_id, task=title, status=task_phase, summary=summary
        ),
        message_type="task",
        metadata=metadata,
    )
    if not is_started and task_id:
        titles.forget(ref.room_id, task_id)


async def plan_steps(
    tools: AgentToolsProtocol, ref: TurnRef, params: dict[str, Any]
) -> None:
    """Forward turn/plan/updated with each step's status."""
    if not (steps := parse_plan_steps(params)):
        return
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=ref.turn_id,
            task="Codex plan",
            status="updated",
            summary=f"{len(steps)} steps",
        ),
        message_type="task",
        metadata={
            "codex_plan_steps": [{"step": s.step, "status": s.status} for s in steps],
            **ref.metadata(),
        },
        log_label="plan steps",
        log_level=logging.DEBUG,
    )


async def token_usage(
    tools: AgentToolsProtocol, ref: TurnRef, usage: CodexTokenUsage | None
) -> None:
    """Report the thread's token usage once Codex has reported any."""
    if usage is None or usage.total_tokens == 0:
        return
    metadata = usage.to_metadata()
    metadata[CodexMetadataKey.THREAD] = ref.thread_id
    metadata[CodexMetadataKey.ROOM] = ref.room_id
    await send_event_safe(
        tools,
        content=usage.format_summary(),
        message_type="task",
        metadata=metadata,
        log_label="token usage event",
        log_level=logging.DEBUG,
    )


def _capped_diff(diff: str) -> tuple[str, dict[str, Any]]:
    """The diff within ``MAX_DIFF_METADATA_BYTES``, and how it was truncated."""
    encoded = diff.encode("utf-8")
    if len(encoded) <= MAX_DIFF_METADATA_BYTES:
        return diff, {}
    # Drop a trailing partial UTF-8 sequence the cut may leave.
    kept = encoded[:MAX_DIFF_METADATA_BYTES].decode("utf-8", errors="ignore")
    dropped = len(encoded) - MAX_DIFF_METADATA_BYTES
    return f"{kept}\n... [truncated, {dropped} more bytes]", {
        "codex_diff_truncated": True,
        "codex_diff_original_length": len(diff),
        "codex_diff_original_bytes": len(encoded),
    }


async def diff_updated(
    tools: AgentToolsProtocol, ref: TurnRef, params: dict[str, Any]
) -> None:
    """Forward turn/diff/updated as a ``turn_diff`` task event."""
    files_raw = params.get("files") or params.get("filesChanged") or []
    files_changed = (
        [str(f) for f in files_raw if f] if isinstance(files_raw, list) else []
    )
    summary = f"{len(files_changed)} files changed" if files_changed else "diff updated"
    diff, truncation = _capped_diff(
        str(params.get("diff") or params.get("content") or "")
    )
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=ref.turn_id, task="Codex diff", status="updated", summary=summary
        ),
        message_type="task",
        metadata={
            "codex_event_type": "turn_diff",
            **ref.metadata(),
            "codex_files_changed": files_changed,
            "codex_diff": diff,
            **truncation,
        },
        log_label="diff event",
        log_level=logging.DEBUG,
    )


async def approval_resolved(
    tools: AgentToolsProtocol, room_id: str, entry: ApprovalAuditEntry
) -> None:
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=str(entry.request_id),
            task="Codex approval",
            status=entry.decision,
            summary=entry.summary,
        ),
        message_type="task",
        metadata={
            "codex_event_type": "approval_resolution",
            "codex_approval_method": entry.method,
            "codex_approval_decision": entry.decision,
            "codex_decided_by": entry.decided_by,
            "codex_session_level": entry.session_level,
            CodexMetadataKey.ROOM: room_id,
            "codex_timestamp": entry.timestamp,
        },
        log_label="approval audit event",
        log_level=logging.DEBUG,
    )


async def error_notification(
    tools: AgentToolsProtocol, ref: TurnRef, params: dict[str, Any]
) -> bool:
    """Report an ``error`` notification unless Codex will retry.

    Returns whether a failure was reported, so a failed ``turn/completed``
    for the same incident is not reported again.
    """
    error = params.get("error") or {}
    if isinstance(error, dict):
        message = error.get("message", "")
    else:
        message = str(error)
        error = {"message": message}
    if params.get("willRetry", False):
        # Logged so a retry storm is observable; the room is not told.
        logger.warning(
            "Codex transient error (will retry, attempt=%s, retry_after=%ss, "
            "thread=%s, turn=%s): %s",
            params.get("retryAttempt") or params.get("attempt"),
            params.get("retryAfterSeconds") or params.get("retryAfter"),
            ref.thread_id,
            ref.turn_id,
            message,
        )
        return False
    logger.error("Codex error: %s", message)
    await tools.send_failure(
        build_agent_failure(
            error, thread_id=ref.thread_id, turn_id=ref.turn_id, room_id=ref.room_id
        )
    )
    return True


async def turn_failed(
    tools: AgentToolsProtocol, ref: TurnRef, turn_payload: dict[str, Any]
) -> None:
    """Report the failure a failed turn/completed carries."""
    error = turn_payload.get("error")
    if not isinstance(error, dict):
        # A falsy scalar carries no message; build_agent_failure's fallback
        # covers it instead of a literal like "None".
        error = {"message": str(error)} if error else {}
    logger.error(
        "Codex turn failed (thread=%s, turn=%s): %s",
        ref.thread_id,
        ref.turn_id,
        error.get("message", ""),
    )
    await tools.send_failure(
        build_agent_failure(
            error, thread_id=ref.thread_id, turn_id=ref.turn_id, room_id=ref.room_id
        )
    )
