"""Pure selection of the ordinary prefix one turn consumes.

Two sources describe a room's outstanding messages: the REST list, which
omits some deliveries (thread replies) and is ordered by ``(inserted_at,
id)``, and the originals the WebSocket delivered and the room retained. The
caller resolves identity and orders the candidates before the walk selects
one frozen, bounded prefix. The caller owns every read, claim and write.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from band.core.timestamps import assume_utc
from band.core.types import metadata_to_dict
from band.platform.event import MessageEvent
from band.runtime.messages import PlatformMessage
from band.runtime.originals import backlog_event, payload


@dataclass(frozen=True, order=True)
class MessageKey:
    """A delivery's chronological position: server insertion time, then id."""

    inserted_at: datetime
    message_id: str

    @classmethod
    def of_message(cls, message: PlatformMessage) -> MessageKey:
        return cls(assume_utc(message.created_at), message.id)

    @classmethod
    def of_event(cls, event: MessageEvent) -> MessageKey:
        original = payload(event)
        return cls(
            assume_utc(datetime.fromisoformat(original.inserted_at)), original.id
        )

    @classmethod
    def last_of(cls, messages: Sequence[PlatformMessage]) -> MessageKey | None:
        """The position a page of rows reaches, or ``None`` for an empty page."""
        return cls.of_message(messages[-1]) if messages else None


class Disposition(StrEnum):
    """What the room may do with a candidate right now."""

    RUNNABLE = "runnable"
    SKIP = "skip"  # settled, failed, self-authored or otherwise not this turn's
    HELD = "held"  # another owner holds its claim; the prefix waits for it
    BOUNDARY = "boundary"  # a control reclassified during this pass stays separate
    CONTROL = "control"  # the adapter handles it individually, never in a batch


@dataclass(frozen=True)
class Candidate:
    """An original and selection facts; observed metadata never rewrites its payload."""

    key: MessageKey
    event: MessageEvent
    disposition: Disposition
    observed_metadata: dict[str, object] | None = None

    @property
    def message_id(self) -> str:
        return self.key.message_id

    @classmethod
    def listed(cls, row: PlatformMessage) -> Candidate:
        """A row the REST list returned, carrying the metadata that read observed."""
        return cls(
            MessageKey.of_message(row),
            backlog_event(row),
            Disposition.RUNNABLE,
            metadata_to_dict(row.metadata),
        )

    @classmethod
    def retained(cls, event: MessageEvent) -> Candidate:
        """An original the WebSocket delivered and the room kept."""
        return cls(MessageKey.of_event(event), event, Disposition.RUNNABLE)


class Stop(StrEnum):
    """Why the prefix ended."""

    CAP = "cap"
    HELD = "held"
    BOUNDARY = "boundary"  # an unmatched retained original runs on its own
    CONTROL = "control"  # a recognized control runs on its own
    FRONTIER = "frontier"  # a retained original lies beyond the page read
    END = "end"


@dataclass(frozen=True)
class Selection:
    """The prefix to run, why it ended, and the candidate that ended it."""

    members: tuple[Candidate, ...]
    stop: Stop
    at: Candidate | None = None


def merge(rest: Sequence[Candidate], retained: Sequence[Candidate]) -> list[Candidate]:
    """Resolve identities and order them by position; an original in both appears once.

    The retained payload is the original that was delivered, so it wins; the
    REST disposition wins because it reflects the platform's delivery status.
    """
    merged = {candidate.message_id: candidate for candidate in rest}
    for candidate in retained:
        seen = merged.get(candidate.message_id)
        merged[candidate.message_id] = (
            candidate
            if seen is None
            else replace(
                candidate,
                key=seen.key,
                disposition=seen.disposition,
                observed_metadata=seen.observed_metadata,
            )
        )
    return sorted(merged.values(), key=lambda candidate: candidate.key)


def select_prefix(
    candidates: Sequence[Candidate],
    *,
    listed: set[str],
    cap: int,
    page_reaches: MessageKey | None,
    exhausted: bool,
    resumed: Collection[str] = (),
) -> Selection:
    """The bounded ordinary prefix from the caller's explicitly ordered input.

    ``page_reaches`` is the key of the last message the REST read returned and
    ``exhausted`` says that read was the last. A retained original absent from
    the read is trusted as a singleton only once the read has reached or
    passed its key (or ended); until then its position against later REST
    messages is unknown, so the prefix stops short of it. ``resumed`` identifies
    confirmed retry continuations whose retained originals keep their position
    without another REST frontier observation.
    """
    members: list[Candidate] = []
    for candidate in candidates:
        unmatched = candidate.message_id not in listed
        if (
            unmatched
            and candidate.message_id not in resumed
            and not read_reaches(candidate, page_reaches, exhausted)
        ):
            return Selection(tuple(members), Stop.FRONTIER, candidate)
        if candidate.disposition is Disposition.SKIP:
            continue
        if candidate.disposition is Disposition.HELD:
            return Selection(tuple(members), Stop.HELD, candidate)
        if unmatched or candidate.disposition in {
            Disposition.CONTROL,
            Disposition.BOUNDARY,
        }:
            stop = (
                Stop.CONTROL
                if not unmatched and candidate.disposition is Disposition.CONTROL
                else Stop.BOUNDARY
            )
            return Selection(tuple(members) or (candidate,), stop, candidate)
        members.append(candidate)
        if len(members) == cap:
            return Selection(tuple(members), Stop.CAP)
    return Selection(tuple(members), Stop.END)


def read_reaches(
    candidate: Candidate, page_reaches: MessageKey | None, exhausted: bool
) -> bool:
    """Whether the REST read has reached or passed the candidate's position."""
    return exhausted or (page_reaches is not None and page_reaches >= candidate.key)
