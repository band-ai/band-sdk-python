"""How a Codex turn's activity reads in the room: tool calls, thoughts, tasks.

The adapter decides which of these its config allows. Tool-call and thought
items are also gated here, on the ``emit`` features the adapter passes in.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from band.adapters.codex.approvals import APPROVAL_OPTIONS, approval_type
from band.adapters.codex.items import (
    THOUGHT_ITEM_TYPES,
    TOOL_ITEM_TYPES,
    UNRENDERED_ITEM_TYPES,
    item_call_id,
    thought_text,
    tool_item,
)
from band.adapters.codex.turnstate import NativeMessage
from band.core.content import has_visible_content
from band.core.protocols import AgentToolsProtocol, send_event_safe
from band.core.types import Emit, MessageType, ToolEventKey, TurnUsage
from band.integrations.codex.types import (
    ApprovalAuditEntry,
    CodexMetadataKey,
    CodexNotificationMethod,
    CodexTokenUsage,
    CodexTurnStatus,
    build_agent_failure,
    parse_plan_steps,
)

logger = logging.getLogger(__name__)

# codex_event_type of both turn lifecycle events.
TURN_LIFECYCLE_EVENT = "turn_lifecycle"

# Bounds the titles remembered between task_started and task_complete.
MAX_TASK_TITLES = 500

# Cap on the raw diff forwarded in turn/diff/updated metadata, in UTF-8 bytes
# so multi-byte content cannot inflate the frame past it. Consumers that need
# the full diff ask Codex for it.
MAX_DIFF_METADATA_BYTES = 64 * 1024


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


# Which payload key carries a tool event's value.
_TOOL_EVENT_KEYS = {
    MessageType.TOOL_CALL: ToolEventKey.ARGS,
    MessageType.TOOL_RESULT: ToolEventKey.OUTPUT,
}


async def tool_event(
    tools: AgentToolsProtocol,
    message_type: MessageType,
    name: str,
    call_id: str,
    value: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Send one tool call's arguments or result to the room."""
    content = {
        ToolEventKey.NAME: name,
        _TOOL_EVENT_KEYS[message_type]: value,
        ToolEventKey.TOOL_CALL_ID: call_id,
    }
    await tools.send_event(
        content=json.dumps(content), message_type=message_type, metadata=metadata
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
        await tool_event(tools, MessageType.TOOL_CALL, name, item_id, args, metadata)
        await tool_event(
            tools, MessageType.TOOL_RESULT, name, item_id, output, metadata
        )
    elif item_type in THOUGHT_ITEM_TYPES:
        # An empty reasoning or plan item says nothing, so it is not shown.
        if Emit.THOUGHTS in emit and (text := thought_text(item_type, item)):
            await tools.send_event(
                content=text, message_type=MessageType.THOUGHT, metadata=metadata
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
    is_started = method == CodexNotificationMethod.TASK_STARTED
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
        message_type=MessageType.TASK,
        metadata=metadata,
    )
    if not is_started and task_id:
        titles.forget(ref.room_id, task_id)


def _turn_measures(duration_s: float, usage: CodexTokenUsage | None) -> dict[str, Any]:
    """A finished turn's duration, once measured, and token usage, once reported."""
    measures: dict[str, Any] = {}
    if duration_s > 0:
        measures["codex_duration_s"] = round(duration_s, 2)
    if usage is not None and usage.total_tokens > 0:
        measures.update(usage.to_metadata())
    return measures


async def turn_marker_started(tools: AgentToolsProtocol, ref: TurnRef) -> None:
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=ref.turn_id,
            task="Codex turn",
            status="started",
            summary=f"Thread: {ref.thread_id}",
        ),
        message_type=MessageType.TASK,
        metadata=ref.metadata(),
        log_label="turn started task event",
        log_level=logging.DEBUG,
    )


async def turn_marker_finished(
    tools: AgentToolsProtocol,
    ref: TurnRef,
    status: str,
    *,
    error: str,
    duration_s: float,
    usage: CodexTokenUsage | None,
) -> None:
    summary = f"Thread: {ref.thread_id}" + (f" | Error: {error}" if error else "")
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=ref.turn_id, task="Codex turn", status=status, summary=summary
        ),
        message_type=MessageType.TASK,
        metadata={
            **ref.metadata(),
            CodexMetadataKey.TURN_STATUS: status,
            CodexMetadataKey.ERROR: error or None,
            **_turn_measures(duration_s, usage),
        },
        log_label="turn outcome task event",
        log_level=logging.DEBUG,
    )


async def turn_lifecycle_started(
    tools: AgentToolsProtocol, ref: TurnRef, input_summary: str
) -> None:
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=ref.turn_id,
            task="Codex turn lifecycle",
            status="started",
            summary=f"Thread: {ref.thread_id}",
        ),
        message_type=MessageType.TASK,
        metadata={
            CodexMetadataKey.EVENT_TYPE: TURN_LIFECYCLE_EVENT,
            **ref.metadata(),
            CodexMetadataKey.TURN_STATUS: "started",
            "codex_input_summary": input_summary,
        },
        log_label="turn started lifecycle event",
        log_level=logging.DEBUG,
    )


async def turn_lifecycle_finished(
    tools: AgentToolsProtocol,
    ref: TurnRef,
    status: str,
    *,
    error: str,
    duration_s: float,
    usage: CodexTokenUsage | None,
) -> None:
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=ref.turn_id,
            task="Codex turn lifecycle",
            status=status,
            summary=f"Duration: {duration_s:.1f}s | Thread: {ref.thread_id}",
        ),
        message_type=MessageType.TASK,
        metadata={
            CodexMetadataKey.EVENT_TYPE: TURN_LIFECYCLE_EVENT,
            **ref.metadata(),
            CodexMetadataKey.TURN_STATUS: status,
            **({CodexMetadataKey.ERROR: error} if error else {}),
            **_turn_measures(duration_s, usage),
        },
        log_label="turn lifecycle event",
        log_level=logging.DEBUG,
    )


async def unstreamed_messages(
    tools: AgentToolsProtocol, ref: TurnRef, messages: dict[str, NativeMessage]
) -> None:
    """Show as thoughts the message text a completed turn never streamed."""
    for item_id, message in messages.items():
        text = message.unstreamed_text
        if not has_visible_content(text):
            continue
        await send_event_safe(
            tools,
            content=text,
            message_type=MessageType.THOUGHT,
            metadata={CodexMetadataKey.ITEM_ID: item_id, **ref.metadata()},
            log_label="Codex completed thought",
        )


def unfinished_turn_text(status: str, error: str) -> str:
    """What the room is told about a turn that did not complete."""
    if status == CodexTurnStatus.INTERRUPTED:
        return "I stopped before completing this request."
    if error:
        return f"I couldn't complete this request ({status}): {error}"
    return f"I couldn't complete this request ({status})."


async def stream_delta(
    tools: AgentToolsProtocol,
    ref: TurnRef,
    item_id: str,
    delta: str,
    *,
    log_label: str,
    subtype: str | None = None,
    event_type: str | None = None,
) -> bool:
    """Stream one delta of an item as a thought; whether it was delivered."""
    labels = {"subtype": subtype, CodexMetadataKey.EVENT_TYPE: event_type}
    return await send_event_safe(
        tools,
        content=delta,
        message_type=MessageType.THOUGHT,
        metadata={
            "streaming": True,
            **{key: value for key, value in labels.items() if value is not None},
            CodexMetadataKey.ITEM_ID: item_id,
            **ref.metadata(),
        },
        log_label=log_label,
        log_level=logging.DEBUG,
    )


async def context_compacted(
    tools: AgentToolsProtocol, ref: TurnRef, params: dict[str, Any]
) -> None:
    """Forward context/compacted, which may name another thread or turn."""
    thread_id = str(params.get("threadId") or ref.thread_id)
    turn_id = str(params.get("turnId") or ref.turn_id or "") or None
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=turn_id,
            task="Codex context compaction",
            status="completed",
            summary=f"Thread: {thread_id}",
        ),
        message_type=MessageType.TASK,
        metadata={
            CodexMetadataKey.EVENT_TYPE: "context_compaction",
            **TurnRef(ref.room_id, thread_id, turn_id).metadata(),
        },
        log_label="context compaction event",
        log_level=logging.DEBUG,
    )


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
        message_type=MessageType.TASK,
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
        message_type=MessageType.TASK,
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
        message_type=MessageType.TASK,
        metadata={
            CodexMetadataKey.EVENT_TYPE: "turn_diff",
            **ref.metadata(),
            "codex_files_changed": files_changed,
            "codex_diff": diff,
            **truncation,
        },
        log_label="diff event",
        log_level=logging.DEBUG,
    )


def _approval_labels(method: str) -> dict[str, Any]:
    return {
        CodexMetadataKey.APPROVAL_METHOD: method,
        CodexMetadataKey.APPROVAL_TYPE: approval_type(method),
        CodexMetadataKey.APPROVAL_OPTIONS: list(APPROVAL_OPTIONS),
    }


async def thread_resumed(
    tools: AgentToolsProtocol, room_id: str, thread_id: str
) -> None:
    await _thread_event(
        tools,
        room_id,
        thread_id,
        status="resumed",
        summary=f"Room: {room_id}",
        details={"codex_resumed": True},
    )


async def thread_mapped(
    tools: AgentToolsProtocol, room_id: str, thread_id: str, transport: str
) -> None:
    await _thread_event(
        tools,
        room_id,
        thread_id,
        status="mapped",
        summary=f"Transport: {transport}",
        details={
            "codex_created_at": datetime.now(UTC).isoformat(),
            CodexMetadataKey.TRANSPORT: transport,
        },
    )


async def _thread_event(
    tools: AgentToolsProtocol,
    room_id: str,
    thread_id: str,
    *,
    status: str,
    summary: str,
    details: dict[str, Any],
) -> None:
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=thread_id, task="Codex thread", status=status, summary=summary
        ),
        message_type=MessageType.TASK,
        metadata={
            CodexMetadataKey.THREAD: thread_id,
            CodexMetadataKey.ROOM: room_id,
            **details,
        },
        log_label=f"thread {status} task event",
        log_level=logging.DEBUG,
    )


async def approval_requested(
    tools: AgentToolsProtocol,
    room_id: str,
    *,
    token: str,
    method: str,
    summary: str,
    params: dict[str, Any],
) -> None:
    """A pending approval as a task event a UI can render as a prompt."""
    metadata: dict[str, Any] = {
        CodexMetadataKey.EVENT_TYPE: "approval_request",
        CodexMetadataKey.ROOM: room_id,
        **_approval_labels(method),
    }
    for key, field in (
        ("command", "codex_command"),
        ("cwd", "codex_cwd"),
        ("reason", "codex_reason"),
    ):
        if params.get(key):
            metadata[field] = params[key]
    if network := params.get("networkContext") or params.get("network_context"):
        metadata["codex_network_context"] = network
    await send_event_safe(
        tools,
        content=task_event_content(
            task_id=token,
            task="Codex approval request",
            status="pending",
            summary=summary,
        ),
        message_type=MessageType.TASK,
        metadata=metadata,
        log_label="approval request task event",
        log_level=logging.DEBUG,
    )


async def approval_decided_by_policy(
    tools: AgentToolsProtocol, method: str, decision: str
) -> None:
    await send_event_safe(
        tools,
        content=f"Codex approval request handled automatically ({decision}).",
        message_type=MessageType.THOUGHT,
        metadata=_approval_labels(method),
        log_label="approval thought event",
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
        message_type=MessageType.TASK,
        metadata={
            CodexMetadataKey.EVENT_TYPE: "approval_resolution",
            CodexMetadataKey.APPROVAL_METHOD: entry.method,
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
