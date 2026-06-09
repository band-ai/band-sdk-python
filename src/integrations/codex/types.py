"""Codex integration types."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Cap on free-form strings copied from Codex error payloads into structured
# event metadata.  ``additionalDetails`` is attacker-influenceable (it echoes
# upstream API errors, prompt content, etc.) and gets rendered by downstream
# UIs, so we bound it before shipping.  2 KiB is generous enough for a stack
# trace or a long error string while keeping WebSocket frames modest.
_MAX_ERROR_DETAIL_CHARS = 2048


# Server-request methods that must never default to anything other than an
# explicit ``decline`` when the adapter can't produce a real decision.
# Shared between the adapter and the SDK bridge so a new approval method is
# added in exactly one place.
CODEX_APPROVAL_METHODS: frozenset[str] = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)


@dataclass
class CodexSessionState:
    """Session state extracted from platform history for Codex rehydration."""

    thread_id: str | None = None
    room_id: str | None = None
    created_at: datetime | None = None

    def has_thread(self) -> bool:
        """Return True when a persisted codex thread_id is available."""
        return bool(self.thread_id)


# ---------------------------------------------------------------------------
# Structured error types
# ---------------------------------------------------------------------------

# Mapping from Codex error type to (human description, suggested action).
CODEX_ERROR_REMEDIATION: dict[str, tuple[str, str]] = {
    "ContextWindowExceeded": (
        "Context window exceeded — the conversation is too long for the model.",
        "compact_context",
    ),
    "UsageLimitExceeded": (
        "Usage limit exceeded — you have hit your API quota.",
        "wait_or_upgrade",
    ),
    "HttpConnectionFailed": (
        "HTTP connection failed — could not reach the API.",
        "check_connectivity",
    ),
    "SandboxError": (
        "Sandbox error — a sandbox policy violation occurred.",
        "review_sandbox_policy",
    ),
    "Unauthorized": (
        "Unauthorized — authentication failed or expired.",
        "re_authenticate",
    ),
    "BadRequest": (
        "Bad request — the input format is invalid.",
        "check_input_format",
    ),
    "ResponseTooManyFailedAttempts": (
        "Too many failed attempts — the model could not produce a valid response.",
        "retry_different_approach",
    ),
}


def build_structured_error_metadata(
    error_obj: dict[str, Any],
    *,
    thread_id: str | None = None,
    turn_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """Parse a Codex error dict and return (content, metadata) for a structured error event.

    The ``error_obj`` is typically the ``error`` field from a turn payload or an
    ``error`` notification.  It may contain a nested ``codexErrorInfo`` dict with
    a ``type`` field identifying the error class.

    ``additionalDetails`` echoes upstream strings that may be attacker-controlled
    (e.g. error messages from a downstream HTTP target) and will be rendered by
    downstream UIs.  Consumers MUST treat the resulting
    ``codex_additional_details`` metadata field as untrusted — escape it before
    rendering as HTML/Markdown.  This helper caps the length at
    ``_MAX_ERROR_DETAIL_CHARS`` (2 KiB) so a hostile payload can't blow up
    WebSocket frames or downstream storage.
    """
    codex_info = error_obj.get("codexErrorInfo") or {}
    if not isinstance(codex_info, dict):
        codex_info = {}
    error_type = codex_info.get("type") or ""
    error_code = codex_info.get("code") or ""
    http_status = codex_info.get("httpStatus")
    is_retryable = bool(codex_info.get("retryable", False))
    additional = error_obj.get("additionalDetails")

    # Look up remediation
    remediation = CODEX_ERROR_REMEDIATION.get(str(error_type))
    if remediation:
        content, suggested_action = remediation
    else:
        raw_message = error_obj.get("message", "")
        content = (
            str(raw_message)
            if raw_message
            else f"Codex error: {error_type or 'unknown'}"
        )
        suggested_action = ""

    metadata: dict[str, Any] = {
        "codex_error_type": error_type or None,
        "codex_error_code": error_code or None,
        "codex_http_status": http_status,
        "codex_is_retryable": is_retryable,
        "codex_suggested_action": suggested_action or None,
    }
    if thread_id:
        metadata["codex_thread_id"] = thread_id
    if turn_id:
        metadata["codex_turn_id"] = turn_id
    if additional is not None:
        capped = _cap_error_detail(additional)
        if capped is not None:
            metadata["codex_additional_details"] = capped

    return content, metadata


def _cap_error_detail(value: Any) -> Any:
    """Cap free-form error detail payloads to ``_MAX_ERROR_DETAIL_CHARS``.

    Strings longer than the cap are truncated with a marker.  Non-string
    JSON-like payloads (dict, list, scalars) are serialized once with
    ``json.dumps`` to measure their footprint; small payloads pass through
    unchanged, oversized payloads are replaced with a string marker so a
    hostile upstream dict can't inflate WebSocket frames past the budget.
    Returns ``None`` to signal "drop this field" for empty/unserializable
    values so callers can ``is not None`` through.
    """
    if isinstance(value, str):
        if not value:
            return None
        if len(value) > _MAX_ERROR_DETAIL_CHARS:
            return (
                value[:_MAX_ERROR_DETAIL_CHARS]
                + f"... [truncated, {len(value) - _MAX_ERROR_DETAIL_CHARS} more chars]"
            )
        return value
    try:
        serialized = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return None
    if len(serialized) > _MAX_ERROR_DETAIL_CHARS:
        return (
            f"[truncated, {len(serialized)} serialized chars exceeded "
            f"{_MAX_ERROR_DETAIL_CHARS}-char cap]"
        )
    return value


# ---------------------------------------------------------------------------
# Plan step tracking
# ---------------------------------------------------------------------------


@dataclass
class CodexPlanStep:
    """A single step in a Codex plan."""

    step: str
    status: str = "pending"  # pending | inProgress | completed


def parse_plan_steps(params: dict[str, Any]) -> list[CodexPlanStep]:
    """Extract plan steps from a turn/plan/updated event payload."""
    plan = params.get("plan")
    if not isinstance(plan, dict):
        plan = params
    steps_raw = plan.get("steps") if isinstance(plan, dict) else None
    if not isinstance(steps_raw, list):
        return []
    steps: list[CodexPlanStep] = []
    for entry in steps_raw:
        if isinstance(entry, dict):
            text = (
                entry.get("text") or entry.get("step") or entry.get("description") or ""
            )
            status = entry.get("status") or "pending"
            if text:
                steps.append(CodexPlanStep(step=str(text), status=str(status)))
        elif isinstance(entry, str) and entry:
            steps.append(CodexPlanStep(step=entry))
    return steps


# ---------------------------------------------------------------------------
# Token usage tracking
# ---------------------------------------------------------------------------


@dataclass
class CodexTokenUsage:
    """Cumulative token usage for a Codex thread.

    Each ``thread/tokenUsage/updated`` event carries cumulative totals,
    so :meth:`update` performs a full replacement (not additive
    accumulation).  Per-turn deltas are measured against an *anchor*
    snapshot of the cumulative counters captured at turn start via
    :meth:`reset_turn_deltas`, so the deltas reflect the rise over the
    whole turn — not just the rise since the previous event.

    Lifecycle:

    1. The adapter creates a ``CodexTokenUsage`` the first time a thread
       emits a token-usage event.
    2. At the start of every turn the adapter calls
       :meth:`reset_turn_deltas`, which captures the current cumulatives
       as the turn anchor and zeroes the ``turn_*`` display fields.
    3. Each ``thread/tokenUsage/updated`` during the turn calls
       :meth:`update`, which replaces the cumulative totals with the
       monotonic max of old and new and recomputes ``turn_*`` as
       ``cumulative - anchor``.  Multiple events in a single turn
       therefore report the growing turn total, not per-event deltas.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0

    # Per-turn deltas (cumulative - anchor, refreshed on each update).
    turn_input_tokens: int = 0
    turn_output_tokens: int = 0
    turn_reasoning_tokens: int = 0
    turn_total_tokens: int = 0

    # Anchor snapshot of cumulative counters, captured at the most recent
    # ``reset_turn_deltas()`` call.  Private: this is the turn-start frame
    # used to derive ``turn_*``, not a user-facing metric.
    _turn_anchor_input: int = field(default=0, repr=False)
    _turn_anchor_output: int = field(default=0, repr=False)
    _turn_anchor_reasoning: int = field(default=0, repr=False)
    _turn_anchor_total: int = field(default=0, repr=False)

    def update(self, params: dict[str, Any]) -> None:
        """Replace counters from a ``thread/tokenUsage/updated`` payload.

        Codex emits **cumulative** totals per thread — each event supersedes
        the previous one — so a full replacement is correct here.
        Per-turn deltas are recomputed as ``cumulative - anchor`` so that
        multi-event turns accumulate correctly.
        """
        usage = params.get("usage") or params
        if not isinstance(usage, dict):
            return

        def _get(key_camel: str, key_snake: str) -> int:
            val = usage.get(key_camel)
            if val is None:
                val = usage.get(key_snake)
            if val is None:
                return 0
            try:
                return int(val)
            except (ValueError, TypeError):
                return 0

        prev_input = self.input_tokens
        prev_output = self.output_tokens
        prev_reasoning = self.reasoning_tokens
        prev_total = self.total_tokens

        new_input = _get("inputTokens", "input_tokens")
        new_output = _get("outputTokens", "output_tokens")
        new_reasoning = _get("reasoningTokens", "reasoning_tokens")
        total = usage.get("totalTokens")
        if total is None:
            total = usage.get("total_tokens")
        if total is not None:
            try:
                new_total = int(total)
            except (ValueError, TypeError):
                new_total = new_input + new_output + new_reasoning
        else:
            new_total = new_input + new_output + new_reasoning

        # Cumulative counters should never go backwards.  Late events from a
        # previous turn (or a protocol regression to delta-shaped payloads)
        # can deliver a smaller cumulative than what we already have; if we
        # replaced the field we would double-count the difference on the next
        # real event (prev=smaller, new=larger → inflated delta).  Warn once
        # and keep the larger of the two so cumulative stays monotonic.
        if (
            new_input < prev_input
            or new_output < prev_output
            or new_reasoning < prev_reasoning
            or new_total < prev_total
        ):
            logger.warning(
                "Codex token usage counter decreased (input %s->%s, output %s->%s, "
                "reasoning %s->%s, total %s->%s). Keeping previous maximum to "
                "preserve monotonic cumulative; protocol may have changed to deltas.",
                prev_input,
                new_input,
                prev_output,
                new_output,
                prev_reasoning,
                new_reasoning,
                prev_total,
                new_total,
            )

        self.input_tokens = max(prev_input, new_input)
        self.output_tokens = max(prev_output, new_output)
        self.reasoning_tokens = max(prev_reasoning, new_reasoning)
        self.total_tokens = max(prev_total, new_total)

        # Per-turn deltas are the rise from the turn-start anchor, not just
        # the rise since the previous event.  Clamp to 0 so a late rewound
        # event (anchor > cumulative) never produces a negative delta.
        self.turn_input_tokens = max(0, self.input_tokens - self._turn_anchor_input)
        self.turn_output_tokens = max(0, self.output_tokens - self._turn_anchor_output)
        self.turn_reasoning_tokens = max(
            0, self.reasoning_tokens - self._turn_anchor_reasoning
        )
        self.turn_total_tokens = max(0, self.total_tokens - self._turn_anchor_total)

    def reset_turn_deltas(self) -> None:
        """Anchor per-turn deltas to the current cumulatives.

        Call at the start of a new turn.  Captures the current cumulative
        counters as the anchor so subsequent :meth:`update` calls report
        ``cumulative - anchor`` as the turn delta, and zeroes the display
        ``turn_*`` fields.
        """
        self._turn_anchor_input = self.input_tokens
        self._turn_anchor_output = self.output_tokens
        self._turn_anchor_reasoning = self.reasoning_tokens
        self._turn_anchor_total = self.total_tokens
        self.turn_input_tokens = 0
        self.turn_output_tokens = 0
        self.turn_reasoning_tokens = 0
        self.turn_total_tokens = 0

    def to_metadata(self) -> dict[str, Any]:
        """Return metadata dict for a token usage event."""
        meta: dict[str, Any] = {
            "codex_event_type": "token_usage",
            "codex_input_tokens": self.input_tokens,
            "codex_output_tokens": self.output_tokens,
            "codex_reasoning_tokens": self.reasoning_tokens,
            "codex_total_tokens": self.total_tokens,
        }
        if self.turn_total_tokens > 0:
            meta["codex_turn_input_tokens"] = self.turn_input_tokens
            meta["codex_turn_output_tokens"] = self.turn_output_tokens
            meta["codex_turn_reasoning_tokens"] = self.turn_reasoning_tokens
            meta["codex_turn_total_tokens"] = self.turn_total_tokens
        return meta

    def format_summary(self) -> str:
        """Human-readable summary."""
        summary = (
            f"Token usage — input: {self.input_tokens:,}, "
            f"output: {self.output_tokens:,}, "
            f"reasoning: {self.reasoning_tokens:,}, "
            f"total: {self.total_tokens:,}"
        )
        if self.turn_total_tokens > 0:
            summary += (
                f" (turn: +{self.turn_input_tokens:,} in, "
                f"+{self.turn_output_tokens:,} out, "
                f"+{self.turn_total_tokens:,} total)"
            )
        return summary


# ---------------------------------------------------------------------------
# Approval audit entry
# ---------------------------------------------------------------------------


@dataclass
class ApprovalAuditEntry:
    """Records an approval decision for audit purposes."""

    request_id: str
    method: str
    decision: str
    decided_by: str
    timestamp: str
    summary: str = ""
    session_level: bool = False
