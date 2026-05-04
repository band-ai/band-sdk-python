"""CrewAI Flow orchestration state contract and converter.

Reconstructs ``CrewAIFlowSessionState`` from a sequence of platform task
events that carry ``metadata[<namespace>]`` payloads. The converter is the
single source of truth for the v1 wire schema; any change to the merge
semantics or field set requires bumping ``schema_version`` and adding a
loader for the older version.

The adapter that produces these events lives in
``src/thenvoi/adapters/crewai_flow.py``.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CrewAIFlowRunStatus(StrEnum):
    OBSERVED = "observed"
    SIDE_EFFECT_RESERVED = "side_effect_reserved"
    DELEGATED_PENDING = "delegated_pending"
    WAITING = "waiting"
    REPLY_RECORDED = "reply_recorded"
    REPLY_AMBIGUOUS = "reply_ambiguous"
    FINALIZED = "finalized"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class CrewAIFlowStage(StrEnum):
    INITIAL = "initial"
    DELEGATED = "delegated"
    WAITING_FOR_REPLIES = "waiting_for_replies"
    DOWNSTREAM_REQUIRED = "downstream_required"
    SYNTHESIZING = "synthesizing"
    DONE = "done"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class CrewAIFlowDelegationStatus(StrEnum):
    RESERVED = "reserved"
    PENDING = "pending"
    REPLIED = "replied"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


class CrewAIFlowJoinPolicy(StrEnum):
    ALL = "all"
    FIRST = "first"


class CrewAIFlowTextOnlyBehavior(StrEnum):
    ERROR_EVENT = "error_event"
    FALLBACK_SEND = "fallback_send"


_TERMINAL_RUN_STATUSES: frozenset[CrewAIFlowRunStatus] = frozenset(
    {
        CrewAIFlowRunStatus.FINALIZED,
        CrewAIFlowRunStatus.FAILED,
        CrewAIFlowRunStatus.INDETERMINATE,
    }
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CrewAIFlowAmbiguousIdentityError(Exception):
    """Raised when two distinct participants normalize to the same key.

    The adapter catches this during reply matching and records
    ``reply_ambiguous`` rather than mutating delegation state.
    """

    def __init__(self, key: str, participant_ids: list[str]) -> None:
        self.key = key
        self.participant_ids = list(participant_ids)
        super().__init__(
            f"Ambiguous participant key {key!r} in room snapshot: {participant_ids}"
        )


# ---------------------------------------------------------------------------
# Pydantic models (v1 wire schema)
# ---------------------------------------------------------------------------


_FORBID = ConfigDict(extra="forbid")


class CrewAIFlowParticipantSnapshot(BaseModel):
    model_config = _FORBID

    participant_id: str
    handle: str | None = None
    normalized_key: str


class CrewAIFlowDelegationState(BaseModel):
    model_config = _FORBID

    delegation_id: str
    target: CrewAIFlowParticipantSnapshot
    status: CrewAIFlowDelegationStatus = CrewAIFlowDelegationStatus.RESERVED
    side_effect_key: str
    reserved_event_id: str | None = None
    delegation_message_id: str | None = None
    sent_event_id: str | None = None
    reply_message_id: str | None = None


class CrewAIFlowSequentialChainState(BaseModel):
    model_config = _FORBID

    upstream_key: str
    downstream_key: str
    status: Literal[
        "not_started", "upstream_replied", "downstream_delegated", "complete"
    ] = "not_started"


class CrewAIFlowBufferedSynthesis(BaseModel):
    model_config = _FORBID

    source_message_id: str
    content: str


class CrewAIFlowSideEffectState(BaseModel):
    model_config = _FORBID

    side_effect_key: str
    reserved_event_id: str | None = None
    message_id: str | None = None
    sent_event_id: str | None = None


class CrewAIFlowError(BaseModel):
    model_config = _FORBID

    code: str
    message: str


class CrewAIFlowMetadata(BaseModel):
    """The on-the-wire ``crewai_flow`` envelope written by the adapter."""

    model_config = _FORBID

    schema_version: Literal[1] = 1
    room_id: str
    run_id: str
    parent_message_id: str
    status: CrewAIFlowRunStatus
    stage: CrewAIFlowStage
    join_policy: CrewAIFlowJoinPolicy = CrewAIFlowJoinPolicy.ALL
    text_only_behavior: CrewAIFlowTextOnlyBehavior = (
        CrewAIFlowTextOnlyBehavior.ERROR_EVENT
    )
    delegations: list[CrewAIFlowDelegationState] = Field(default_factory=list)
    sequential_chains: list[CrewAIFlowSequentialChainState] = Field(
        default_factory=list
    )
    buffered_syntheses: list[CrewAIFlowBufferedSynthesis] = Field(default_factory=list)
    side_effects: list[CrewAIFlowSideEffectState] = Field(default_factory=list)
    tagged_peer_keys: list[str] = Field(default_factory=list)
    delegation_rounds: int = 0
    final_side_effect_key: str | None = None
    final_reserved_event_id: str | None = None
    final_message_id: str | None = None
    final_sent_event_id: str | None = None
    error: CrewAIFlowError | None = None


class CrewAIFlowSessionState(BaseModel):
    """Aggregate state per room: one ``CrewAIFlowMetadata`` per ``run_id``.

    Not part of the wire format. Produced by ``CrewAIFlowStateConverter``.
    """

    model_config = ConfigDict(extra="forbid")

    runs: dict[str, CrewAIFlowMetadata] = Field(default_factory=dict)

    def is_terminal(self, run_id: str) -> bool:
        run = self.runs.get(run_id)
        return run is not None and run.status in _TERMINAL_RUN_STATUSES

    def active_runs(self) -> list[CrewAIFlowMetadata]:
        return [r for r in self.runs.values() if r.status not in _TERMINAL_RUN_STATUSES]


# ---------------------------------------------------------------------------
# Participant-key normalization
# ---------------------------------------------------------------------------


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _basic_normalize(raw: str) -> str:
    s = raw.strip()
    if s.startswith("@"):
        s = s[1:]
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    return s.strip().lower()


def normalize_participant_key(
    raw: str,
    *,
    participants: list[dict[str, Any]] | None = None,
) -> str:
    """Normalize a raw selector to a canonical participant key.

    Steps (per spec):
      1. Strip leading '@' if present.
      2. If '/' in remainder, take the segment after the last '/'.
      3. Lowercase.
      4. Strip leading and trailing whitespace.

    If a room participant snapshot is supplied and ``raw`` matches a
    participant.id (UUID), the function returns the normalized handle of
    that participant instead — so UUID, namespaced handle, bare handle,
    and display name all resolve to the same canonical key.

    Raises ``CrewAIFlowAmbiguousIdentityError`` when the resulting key
    collides between two distinct participants in the snapshot.
    """
    raw_str = (raw or "").strip()
    if not raw_str:
        return ""

    if participants and _UUID_RE.match(raw_str):
        # UUID match: route through the participant's handle.
        for p in participants:
            if str(p.get("id", "")).lower() == raw_str.lower():
                handle = p.get("handle") or p.get("name") or ""
                resolved = _basic_normalize(handle) if handle else ""
                if resolved:
                    return _check_collisions(resolved, participants)
                break

    base = _basic_normalize(raw_str)
    if participants is None:
        return base

    # Display-name fallback: if the input matches a participant's name (case
    # insensitive) but does not match a handle, route through that
    # participant's handle.
    for p in participants:
        name = (p.get("name") or "").strip().lower()
        if name and name == raw_str.strip().lower():
            handle = p.get("handle")
            if handle:
                base = _basic_normalize(handle)
                break

    return _check_collisions(base, participants)


def _check_collisions(key: str, participants: list[dict[str, Any]]) -> str:
    """Raise if two distinct participants normalize to the same key."""
    matches: list[str] = []
    for p in participants:
        for candidate in (p.get("handle"), p.get("name")):
            if not candidate:
                continue
            if _basic_normalize(candidate) == key:
                pid = str(p.get("id") or p.get("handle") or p.get("name"))
                if pid not in matches:
                    matches.append(pid)
                break
    if len(matches) > 1:
        raise CrewAIFlowAmbiguousIdentityError(key, matches)
    return key


# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------


def _coerce_inserted_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _sort_key(event: dict[str, Any]) -> tuple[datetime, str]:
    inserted = _coerce_inserted_at(event.get("inserted_at"))
    if inserted is None:
        inserted = datetime.fromtimestamp(0, tz=timezone.utc)
    return (inserted, str(event.get("id") or event.get("message_id") or ""))


class CrewAIFlowStateConverter:
    """Reconstruct ``CrewAIFlowSessionState`` from raw task-event dicts.

    The converter is namespace-aware. Events whose
    ``metadata[<metadata_namespace>]`` is absent are ignored; events with a
    different namespace key are also ignored. Events under the configured
    namespace are sorted by ``(inserted_at, message_id)`` ascending and
    folded into per-run ``CrewAIFlowMetadata`` records using last-write-wins
    on scalar fields and stable-key merging on list fields. Once a run
    reaches a terminal status, later events for that run are ignored with a
    warning log.

    Args:
        metadata_namespace: top-level metadata key to scan for. Defaults to
            ``"crewai_flow"`` for backwards-compatible behavior; the adapter
            overrides this with ``f"crewai_flow:{agent_id}"`` to isolate
            runs across agents in the same room.
        max_run_age: when set, non-terminal runs whose
            ``parent_message_id`` timestamp is older than ``now -
            max_run_age`` are closed with ``failed``/``run_aged_out``. The
            ``parent_message_inserted_at`` per run is taken from the
            earliest event observed for that run. ``None`` disables the
            check (used in tests).
    """

    def __init__(
        self,
        *,
        metadata_namespace: str = "crewai_flow",
        max_run_age: timedelta | None = None,
        agent_name: str | None = None,
    ) -> None:
        self.metadata_namespace = metadata_namespace
        self.max_run_age = max_run_age
        self._agent_name = agent_name

    def set_agent_name(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def convert(self, raw: list[dict[str, Any]]) -> CrewAIFlowSessionState:
        if not raw:
            return CrewAIFlowSessionState()

        # Filter and sort.
        candidate: list[tuple[datetime, str, dict[str, Any], dict[str, Any]]] = []
        for event in raw:
            metadata = event.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            payload = metadata.get(self.metadata_namespace)
            if payload is None:
                continue
            inserted, msg_id = _sort_key(event)
            candidate.append((inserted, msg_id, event, payload))

        candidate.sort(key=lambda t: (t[0], t[1]))

        runs: dict[str, CrewAIFlowMetadata] = {}
        run_first_seen_inserted_at: dict[str, datetime] = {}
        warned_terminal: set[str] = set()

        for inserted, _msg_id, event, payload in candidate:
            try:
                incoming = self._validate_payload(payload)
            except ValidationError as exc:
                run_id = self._best_effort_run_id(payload, event)
                existing = runs.get(run_id)
                if existing is not None and existing.status in _TERMINAL_RUN_STATUSES:
                    if run_id not in warned_terminal:
                        logger.warning(
                            "Ignoring malformed event for terminal run %s (status=%s)",
                            run_id,
                            existing.status,
                        )
                        warned_terminal.add(run_id)
                    continue
                self._record_malformed(runs, run_id, payload, event, exc)
                continue

            run_id = incoming.run_id
            run_first_seen_inserted_at.setdefault(run_id, inserted)
            existing = runs.get(run_id)
            if existing is None:
                runs[run_id] = incoming
                continue
            if existing.status in _TERMINAL_RUN_STATUSES:
                if run_id not in warned_terminal:
                    logger.warning(
                        "Ignoring later event for terminal run %s (status=%s)",
                        run_id,
                        existing.status,
                    )
                    warned_terminal.add(run_id)
                continue
            runs[run_id] = self._merge(existing, incoming)

        if self.max_run_age is not None:
            now = datetime.now(timezone.utc)
            for run_id, run in list(runs.items()):
                if run.status in _TERMINAL_RUN_STATUSES:
                    continue
                first_seen = run_first_seen_inserted_at.get(run_id)
                if first_seen is None:
                    continue
                if now - first_seen > self.max_run_age:
                    runs[run_id] = run.model_copy(
                        update={
                            "status": CrewAIFlowRunStatus.FAILED,
                            "stage": CrewAIFlowStage.FAILED,
                            "error": CrewAIFlowError(
                                code="run_aged_out",
                                message=(
                                    f"Run exceeded max_run_age "
                                    f"({self.max_run_age}); proactively closed."
                                ),
                            ),
                        }
                    )

        return CrewAIFlowSessionState(runs=runs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_payload(self, payload: dict[str, Any]) -> CrewAIFlowMetadata:
        return CrewAIFlowMetadata.model_validate(payload)

    def _best_effort_run_id(
        self, payload: dict[str, Any], event: dict[str, Any]
    ) -> str:
        run_id = payload.get("run_id") if isinstance(payload, dict) else None
        if isinstance(run_id, str) and run_id:
            return run_id
        return f"unknown:{event.get('id') or uuid.uuid4().hex}"

    def _record_malformed(
        self,
        runs: dict[str, CrewAIFlowMetadata],
        run_id: str,
        payload: dict[str, Any],
        event: dict[str, Any],
        exc: ValidationError,
    ) -> None:
        logger.warning(
            "Malformed crewai_flow metadata on event %s: %s",
            event.get("id"),
            exc,
        )
        room_id = ""
        parent_message_id = ""
        if isinstance(payload, dict):
            room_id = str(payload.get("room_id") or "")
            parent_message_id = str(payload.get("parent_message_id") or "")
        runs[run_id] = CrewAIFlowMetadata(
            room_id=room_id,
            run_id=run_id,
            parent_message_id=parent_message_id or run_id,
            status=CrewAIFlowRunStatus.FAILED,
            stage=CrewAIFlowStage.FAILED,
            error=CrewAIFlowError(
                code="malformed_metadata",
                message=str(exc)[:500],
            ),
        )

    @staticmethod
    def _merge(
        existing: CrewAIFlowMetadata, incoming: CrewAIFlowMetadata
    ) -> CrewAIFlowMetadata:
        # Merge delegations by delegation_id.
        delegations = {d.delegation_id: d for d in existing.delegations}
        for d in incoming.delegations:
            delegations[d.delegation_id] = d

        # Merge sequential chains by (upstream_key, downstream_key).
        chains: dict[tuple[str, str], CrewAIFlowSequentialChainState] = {
            (c.upstream_key, c.downstream_key): c for c in existing.sequential_chains
        }
        for c in incoming.sequential_chains:
            chains[(c.upstream_key, c.downstream_key)] = c

        # Merge buffered syntheses by source_message_id (preserve ordering by
        # appending unseen entries from incoming after existing).
        seen_sources = {b.source_message_id for b in existing.buffered_syntheses}
        merged_buffered = list(existing.buffered_syntheses)
        for b in incoming.buffered_syntheses:
            if b.source_message_id not in seen_sources:
                merged_buffered.append(b)
                seen_sources.add(b.source_message_id)

        side_effects = {s.side_effect_key: s for s in existing.side_effects}
        for s in incoming.side_effects:
            current = side_effects.get(s.side_effect_key)
            if current is None:
                side_effects[s.side_effect_key] = s
                continue
            side_effects[s.side_effect_key] = current.model_copy(
                update={
                    "reserved_event_id": s.reserved_event_id
                    or current.reserved_event_id,
                    "message_id": s.message_id or current.message_id,
                    "sent_event_id": s.sent_event_id or current.sent_event_id,
                }
            )

        return existing.model_copy(
            update={
                "status": incoming.status,
                "stage": incoming.stage,
                "join_policy": incoming.join_policy,
                "text_only_behavior": incoming.text_only_behavior,
                "delegations": list(delegations.values()),
                "sequential_chains": list(chains.values()),
                "buffered_syntheses": merged_buffered,
                "side_effects": list(side_effects.values()),
                "tagged_peer_keys": incoming.tagged_peer_keys
                or existing.tagged_peer_keys,
                "delegation_rounds": max(
                    existing.delegation_rounds,
                    incoming.delegation_rounds,
                ),
                "final_side_effect_key": (
                    incoming.final_side_effect_key or existing.final_side_effect_key
                ),
                "final_reserved_event_id": (
                    incoming.final_reserved_event_id or existing.final_reserved_event_id
                ),
                "final_message_id": (
                    incoming.final_message_id or existing.final_message_id
                ),
                "final_sent_event_id": (
                    incoming.final_sent_event_id or existing.final_sent_event_id
                ),
                "error": incoming.error or existing.error,
            }
        )


__all__ = [
    "CrewAIFlowAmbiguousIdentityError",
    "CrewAIFlowBufferedSynthesis",
    "CrewAIFlowDelegationState",
    "CrewAIFlowDelegationStatus",
    "CrewAIFlowError",
    "CrewAIFlowJoinPolicy",
    "CrewAIFlowMetadata",
    "CrewAIFlowParticipantSnapshot",
    "CrewAIFlowRunStatus",
    "CrewAIFlowSequentialChainState",
    "CrewAIFlowSessionState",
    "CrewAIFlowSideEffectState",
    "CrewAIFlowStage",
    "CrewAIFlowStateConverter",
    "CrewAIFlowTextOnlyBehavior",
    "normalize_participant_key",
]
