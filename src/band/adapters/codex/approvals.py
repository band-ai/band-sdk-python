"""Codex approval requests: their wire shapes and each room's approval state."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from band.integrations.codex.types import (
    ApprovalAuditEntry,
    CodexApprovalMethod,
    CodexItemType,
)
from band.runtime.decisions import DecisionEntry, DecisionRegistry, Registration

ApprovalDecision = Literal["accept", "acceptForSession", "decline"]
SessionApprovalGranularity = Literal["binary", "full_command"]
APPROVAL_OPTIONS: tuple[ApprovalDecision, ...] = (
    "accept",
    "acceptForSession",
    "decline",
)


def approval_response(decision: ApprovalDecision) -> dict[str, Any]:
    return {"decision": decision}


@dataclass
class PendingApproval:
    request_id: int | str
    method: str
    summary: str
    created_at: datetime
    future: asyncio.Future[ApprovalDecision]
    session_key: str = ""
    native_key: str = ""


def approval_summary(method: str, params: dict[str, Any]) -> str:
    match method:
        case CodexApprovalMethod.COMMAND_EXECUTION:
            command = params.get("command")
            if isinstance(command, str) and command:
                return f"command: {command}"
            return "command execution"
        case CodexApprovalMethod.FILE_CHANGE:
            reason = params.get("reason")
            if isinstance(reason, str) and reason:
                return f"file changes: {reason}"
            return "file changes"
        case _:
            return method


def approval_type(method: str) -> str:
    """A short label for the approval request type."""
    match method:
        case CodexApprovalMethod.COMMAND_EXECUTION:
            return CodexItemType.COMMAND_EXECUTION.value
        case CodexApprovalMethod.FILE_CHANGE:
            return CodexItemType.FILE_CHANGE.value
        case _:
            return method


def native_approval_key(request_id: int | str, params: dict[str, Any]) -> str:
    for key in ("approvalId", "approval_id", "itemId", "callId"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return f"req-{request_id}"


def session_approval_key(
    method: str, params: dict[str, Any], granularity: SessionApprovalGranularity
) -> str:
    """The key ``/approve-session`` matches later requests by.

    ``"full_command"`` keys on the whole command, so approving ``npm test``
    covers only ``npm test``; ``"binary"`` keys on its first token, covering
    every ``npm`` command. File changes key on the sorted set of paths.

    A request with no command or paths, or of an unknown method, has no key
    (""), so session-level approval is refused: keying on the bare method
    would approve every future request of that kind.
    """
    match method:
        case CodexApprovalMethod.COMMAND_EXECUTION:
            command = params.get("command")
            if not (isinstance(command, str) and command.strip()):
                return ""
            command = command.strip()
            if granularity == "binary":
                command = command.split()[0]
            return f"commandExecution:{command}"
        case CodexApprovalMethod.FILE_CHANGE:
            paths = _file_change_paths(params)
            return "fileChange:" + "|".join(sorted(paths)) if paths else ""
        case _:
            return ""


def _file_change_paths(params: dict[str, Any]) -> list[str]:
    """File paths from an item/fileChange/requestApproval payload."""
    paths: list[str] = []
    changes = params.get("changes")
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                path = change.get("path")
                if isinstance(path, str) and path:
                    paths.append(path)
    # Some payload variants carry paths at the top level.
    for key in ("path", "paths"):
        value = params.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
        elif isinstance(value, list):
            paths.extend(p for p in value if isinstance(p, str) and p)
    return paths


@dataclass
class CodexApprovals:
    """Each room's pending approvals, session-level approvals and audit trail.

    Callers mutate a room's state only from that room's turn or approval
    command, so no lock is needed.
    """

    max_pending: int
    max_session_approved: int
    max_audit: int
    granularity: SessionApprovalGranularity
    # A registry outlives room disposal so its tokens are never reminted.
    _pending: dict[str, DecisionRegistry[PendingApproval]] = field(
        default_factory=dict, init=False
    )
    # Ordered sets: the least recently approved key is evicted first.
    _session: dict[str, OrderedDict[str, None]] = field(
        default_factory=dict, init=False
    )
    _audit: dict[str, list[ApprovalAuditEntry]] = field(
        default_factory=dict, init=False
    )

    def session_key(self, method: str, params: dict[str, Any]) -> str:
        return session_approval_key(method, params, self.granularity)

    def is_session_approved(self, room_id: str, key: str) -> bool:
        return bool(key) and key in self._session.get(room_id, ())

    def approve_for_session(self, room_id: str, key: str) -> None:
        """Approve ``key`` for the room's session, as its most recent approval."""
        approved = self._session.setdefault(room_id, OrderedDict())
        approved[key] = None
        approved.move_to_end(key)
        while len(approved) > self.max_session_approved:
            approved.popitem(last=False)

    def session_patterns(self, room_id: str) -> list[str]:
        return sorted(self._session.get(room_id, ()))

    def record(
        self,
        room_id: str,
        *,
        request_id: str,
        method: str,
        decision: str,
        decided_by: str,
        summary: str = "",
        session_level: bool = False,
    ) -> ApprovalAuditEntry:
        """Record a decision in the room's capped audit trail."""
        entry = ApprovalAuditEntry(
            request_id=request_id,
            method=method,
            decision=decision,
            decided_by=decided_by,
            timestamp=datetime.now(UTC).isoformat(),
            summary=summary,
            session_level=session_level,
        )
        audit = self._audit.setdefault(room_id, [])
        audit.append(entry)
        if len(audit) > self.max_audit:
            del audit[: len(audit) - self.max_audit]
        return entry

    def audit(self, room_id: str) -> list[ApprovalAuditEntry]:
        return list(self._audit.get(room_id, ()))

    def registry(self, room_id: str) -> DecisionRegistry[PendingApproval]:
        return self._pending.setdefault(
            room_id, DecisionRegistry(max_pending=self.max_pending)
        )

    def register(
        self, room_id: str, pending: PendingApproval
    ) -> Registration[PendingApproval] | None:
        """Register a request, or its redelivery under its original token.

        None when a reply already claimed the redelivered request.
        """
        registry = self.registry(room_id)
        for token, existing in registry.items():
            if existing.native_key == pending.native_key:
                return registry.register_keyed(pending, key=token)
        return registry.register_minted(pending)

    def pending(self, room_id: str) -> DecisionRegistry[PendingApproval]:
        """The room's registry, or an empty one when it has never had a request."""
        return self._pending.get(room_id) or DecisionRegistry()

    def open_count(self, room_id: str) -> int:
        return self.pending(room_id).unclaimed_count()

    def forget(self, room_id: str, entry: DecisionEntry[PendingApproval]) -> None:
        if (registry := self._pending.get(room_id)) is not None:
            registry.forget(entry)

    def decline_all(self, room_id: str) -> None:
        """Decline every pending request; nothing can answer them any more."""
        for entry in self.pending(room_id).cancel_all():
            entry.payload.future.set_result("decline")

    def forget_room(self, room_id: str) -> None:
        """Erase what only a final leave ends; the registry's tokens stay spent."""
        self.decline_all(room_id)
        self._session.pop(room_id, None)
        self._audit.pop(room_id, None)
