"""Host-tool decline refusal text for native OMP backlog and sole-tool proof."""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from dataclasses import dataclass

from band.integrations.ompwire import OmpContent, OmpContentKind, OmpMessage, OmpState
from band.runtime.tools import BandTool

NATIVE_BACKLOG_YIELD_GUIDANCE = (
    "If delivery is the only remaining action, end the current model step without "
    "further tool calls so OMP can deliver queued follow-ups or async completions. "
    "This native yield is not a Band decline or a claim that the turn is complete."
)

NATIVE_REFUSED_DECLINE_GUIDANCE = (
    "This turn has not declined. Do not poll this tool or claim completion in "
    "plain text."
)

NATIVE_QUEUED_INPUT_GUIDANCE = (
    "Queued native messages may contain participant input, not just tool results; "
    "their contents cannot be inferred from tool or task inventory."
)

PENDING_ASYNC_WORK_LABEL = "hasPendingAsyncWork=true"
QUEUED_MESSAGE_COUNT_PREFIX = "queuedMessageCount="

SOLE_TOOL_SIBLING_RECOVERY = (
    "Finish the other outstanding tool calls and handle their results before "
    f"calling {BandTool.NO_REPLY} alone."
)
SOLE_TOOL_UNSTRUCTURED_RECOVERY = (
    "There are no sibling tool calls to finish from this message; retry "
    f"{BandTool.NO_REPLY} only as a structured sole tool call when the whole "
    "turn needs no reply."
)
SOLE_TOOL_ID_MISMATCH_RECOVERY = (
    "The assistant message names a different tool call id; call "
    f"{BandTool.NO_REPLY} only as the sole tool when the whole turn needs no reply."
)

RECONSIDER_PENDING_WORK = (
    "Reconsider whether a reply is needed after processing the pending work."
)
RECONSIDER_PENDING_INPUT = (
    "Reconsider whether a reply is needed after processing the pending input."
)
RECONSIDER_PENDING_WORK_AND_INPUT = (
    "Reconsider whether a reply is needed after processing the pending work and input."
)


def queued_message_count_label(count: int) -> str:
    """Wire fragment naming a non-zero native queued-message backlog."""
    return f"{QUEUED_MESSAGE_COUNT_PREFIX}{count}"


def tool_call_id_label(tool_call_id: str) -> str:
    """Wire fragment naming the decline tool-call id under proof."""
    return f"toolCallId={tool_call_id}"


def compose_decline_refusals(backlog: str | None, sole: str | None) -> str | None:
    """Join observed decline refusal texts into one host-tool error."""
    texts = [part for part in (backlog, sole) if part is not None]
    return " ".join(texts) if texts else None


def assistant_tool_calls(message: OmpMessage | None) -> tuple[OmpContent, ...] | None:
    """Return structured TOOL_CALL blocks, or None when content is unstructured."""
    if message is None or isinstance(message.content, str):
        return None
    return tuple(
        block for block in message.content if block.type == OmpContentKind.TOOL_CALL
    )


def unfinished_tool_calls(
    message: OmpMessage | None,
    finished_tools: AbstractSet[str] = frozenset(),
) -> tuple[OmpContent, ...] | None:
    """Structured TOOL_CALL blocks that have not yet finished, or None if unstructured."""
    calls = assistant_tool_calls(message)
    if calls is None:
        return None
    return tuple(block for block in calls if block.id not in finished_tools)


def backlog_reconsider_clause(*, has_work: bool, has_input: bool) -> str:
    match (has_work, has_input):
        case (True, True):
            return RECONSIDER_PENDING_WORK_AND_INPUT
        case (True, False):
            return RECONSIDER_PENDING_WORK
        case (False, True):
            return RECONSIDER_PENDING_INPUT
        case _:
            raise ValueError("backlog reconsider requires at least one blocker")


@dataclass(frozen=True)
class BacklogObservation:
    blockers: tuple[str, ...]
    recovery: tuple[str, ...]
    has_work: bool
    has_input: bool


def observe_backlog(state: OmpState) -> BacklogObservation | None:
    """Collect named backlog blockers and recovery steps, or None when idle."""
    has_work = state.hasPendingAsyncWork
    has_input = state.queuedMessageCount > 0
    if not (has_work or has_input):
        return None
    blockers: list[str] = []
    recovery: list[str] = []
    if has_work:
        blockers.append(PENDING_ASYNC_WORK_LABEL)
        recovery.append("Wait for the existing async work and handle its completion.")
    if has_input:
        blockers.append(queued_message_count_label(state.queuedMessageCount))
        recovery.append(
            "Allow OMP to deliver the queued messages on continuation, then read and "
            f"handle them. {NATIVE_QUEUED_INPUT_GUIDANCE}"
        )
    return BacklogObservation(
        blockers=tuple(blockers),
        recovery=tuple(recovery),
        has_work=has_work,
        has_input=has_input,
    )


def format_backlog_refusal(observed: BacklogObservation, *, allow_yield: bool) -> str:
    parts = [
        f"{BandTool.NO_REPLY} refused: native {' '.join(observed.blockers)}.",
        " ".join(observed.recovery),
        NATIVE_REFUSED_DECLINE_GUIDANCE,
    ]
    if allow_yield:
        parts.append(NATIVE_BACKLOG_YIELD_GUIDANCE)
    parts.append(
        backlog_reconsider_clause(
            has_work=observed.has_work, has_input=observed.has_input
        )
    )
    return " ".join(parts)


def decline_backlog_refusal(state: OmpState, *, allow_yield: bool = True) -> str | None:
    """Describe only the native backlog observed in this snapshot."""
    observed = observe_backlog(state)
    if observed is None:
        return None
    return format_backlog_refusal(observed, allow_yield=allow_yield)


def sole_tool_diagnosis(
    message: OmpMessage | None,
    tool_call_id: str,
    *,
    finished_tools: AbstractSet[str] = frozenset(),
) -> tuple[str, str] | None:
    """Return (diagnosis, recovery) when sole-tool proof fails, else None."""
    label = tool_call_id_label(tool_call_id)
    match unfinished_tool_calls(message, finished_tools):
        case None:
            return (
                f"no sole-tool-call proof for {label} (string or missing content)",
                SOLE_TOOL_UNSTRUCTURED_RECOVERY,
            )
        case []:
            return (
                f"no sole-tool-call proof for {label} (no TOOL_CALL blocks)",
                SOLE_TOOL_UNSTRUCTURED_RECOVERY,
            )
        case [only] if only.id == tool_call_id:
            return None
        case [only]:
            diagnosis = (
                f"no sole-tool-call proof for {label} "
                f"(assistant {tool_call_id_label(only.id or '')})"
            )
            return diagnosis, SOLE_TOOL_ID_MISMATCH_RECOVERY
        case many:
            diagnosis = (
                f"no sole-tool-call proof for {label} "
                f"({len(many)} TOOL_CALL blocks in the assistant message)"
            )
            return diagnosis, SOLE_TOOL_SIBLING_RECOVERY


def format_sole_tool_refusal(diagnosis: str, recovery: str) -> str:
    return (
        f"{BandTool.NO_REPLY} refused: {diagnosis}. {recovery} "
        f"{NATIVE_REFUSED_DECLINE_GUIDANCE}"
    )


def decline_sole_tool_refusal(
    message: OmpMessage | None,
    tool_call_id: str,
    *,
    finished_tools: AbstractSet[str] = frozenset(),
) -> str | None:
    """Describe why the decline is not the sole tool call, or None when it is."""
    diagnosed = sole_tool_diagnosis(
        message, tool_call_id, finished_tools=finished_tools
    )
    if diagnosed is None:
        return None
    diagnosis, recovery = diagnosed
    return format_sole_tool_refusal(diagnosis, recovery)


def decline_running_tools_refusal(
    tool_call_id: str, running_tools: AbstractSet[str]
) -> str | None:
    """Refuse when the declining call is not the only native tool still running."""
    if running_tools == {tool_call_id}:
        return None
    label = tool_call_id_label(tool_call_id)
    if tool_call_id not in running_tools:
        diagnosis = (
            f"no sole-tool-call proof for {label} "
            "(declining call is not a running native tool)"
        )
    else:
        diagnosis = (
            f"no sole-tool-call proof for {label} "
            f"({len(running_tools)} native tools running)"
        )
    return format_sole_tool_refusal(diagnosis, SOLE_TOOL_SIBLING_RECOVERY)


def decline_sibling_callback_refusal(
    tool_call_id: str, *, has_sibling_callbacks: bool
) -> str | None:
    """Refuse when another host callback is still registered beside the decline."""
    if not has_sibling_callbacks:
        return None
    label = tool_call_id_label(tool_call_id)
    return format_sole_tool_refusal(
        f"no sole-tool-call proof for {label} (other host callbacks remain)",
        SOLE_TOOL_SIBLING_RECOVERY,
    )
