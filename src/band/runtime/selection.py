"""Pure selection of the ordinary prefix one turn consumes.

Two sources describe a room's outstanding messages: the REST list, which
omits some deliveries (thread replies) and is ordered by ``(inserted_at,
id)``, and the originals the WebSocket delivered and the room retained. The
walk below merges them by that key into one frozen, bounded prefix; the
caller owns every read, claim and write.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from band.core.timestamps import assume_utc
from band.platform.event import MessageEvent
from band.runtime.messages import PlatformMessage


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
        if event.payload is None:
            raise ValueError("a message event without a payload has no position")
        return cls(
            assume_utc(datetime.fromisoformat(event.payload.inserted_at)),
            event.payload.id,
        )


class Disposition(StrEnum):
    """What the room may do with a candidate right now."""

    RUNNABLE = "runnable"
    SKIP = "skip"  # settled, failed, self-authored or otherwise not this turn's
    HELD = "held"  # another owner holds its claim; the prefix waits for it
    CONTROL = "control"  # the adapter handles it individually, never in a batch


@dataclass(frozen=True)
class Candidate:
    """One original with its position and disposition."""

    key: MessageKey
    event: MessageEvent
    disposition: Disposition

    @property
    def message_id(self) -> str:
        return self.key.message_id


class Stop(StrEnum):
    """Why the prefix ended."""

    CAP = "cap"
    HELD = "held"
    BOUNDARY = "boundary"  # an unmatched retained original runs on its own
    CONTROL = "control"  # a recognized control runs on its own
    FRONTIER = "frontier"  # a retained original lies beyond the page read
    LIMIT = "limit"  # a later local segment starts here
    END = "end"


@dataclass(frozen=True)
class Selection:
    """The prefix to run, why it ended, and the candidate that ended it."""

    members: tuple[Candidate, ...]
    stop: Stop
    at: Candidate | None = None

    @property
    def events(self) -> tuple[MessageEvent, ...]:
        return tuple(candidate.event for candidate in self.members)


def merge(rest: Sequence[Candidate], retained: Sequence[Candidate]) -> list[Candidate]:
    """One chronological list; an original in both sources appears once.

    The retained payload is the original that was delivered, so it wins; the
    REST disposition wins because it reflects the platform's delivery status.
    """
    merged = {candidate.message_id: candidate for candidate in rest}
    for candidate in retained:
        seen = merged.get(candidate.message_id)
        merged[candidate.message_id] = (
            candidate
            if seen is None
            else replace(candidate, key=seen.key, disposition=seen.disposition)
        )
    return sorted(merged.values(), key=lambda candidate: candidate.key)


def select_prefix(
    rest: Sequence[Candidate],
    retained: Sequence[Candidate],
    *,
    cap: int,
    page_reaches: MessageKey | None,
    exhausted: bool,
    limit: MessageKey | None = None,
) -> Selection:
    """The bounded ordinary prefix to run now, in chronological order.

    ``page_reaches`` is the key of the last message the REST read returned and
    ``exhausted`` says that read was the last. A retained original absent from
    the read is trusted as a singleton only once the read has reached or
    passed its key (or ended); until then its position against later REST
    messages is unknown, so the prefix stops short of it. ``limit`` bounds the
    REST-only candidates to the current local segment. Retained candidates are
    already known to belong before its boundary, regardless of their keys.
    """
    members: list[Candidate] = []
    rest_ids = {candidate.message_id for candidate in rest}
    retained_ids = {candidate.message_id for candidate in retained}
    limited_at: Candidate | None = None
    for candidate in merge(rest, retained):
        if (
            limit is not None
            and candidate.key > limit
            and candidate.message_id not in retained_ids
        ):
            if limited_at is None:
                limited_at = candidate
            continue
        if candidate.disposition is Disposition.SKIP:
            continue
        if candidate.disposition is Disposition.HELD:
            return Selection(tuple(members), Stop.HELD, candidate)
        unmatched = candidate.message_id not in rest_ids
        if unmatched and not _read_reaches(candidate, page_reaches, exhausted):
            return Selection(tuple(members), Stop.FRONTIER, candidate)
        if unmatched or candidate.disposition is Disposition.CONTROL:
            stop = Stop.BOUNDARY if unmatched else Stop.CONTROL
            return Selection(tuple(members) or (candidate,), stop, candidate)
        members.append(candidate)
        if len(members) == cap:
            return Selection(tuple(members), Stop.CAP)
    if limited_at is not None:
        return Selection(tuple(members), Stop.LIMIT, limited_at)
    return Selection(tuple(members), Stop.END)


def _read_reaches(
    candidate: Candidate, page_reaches: MessageKey | None, exhausted: bool
) -> bool:
    """Whether the REST read has reached or passed the candidate's position."""
    return exhausted or (page_reaches is not None and page_reaches >= candidate.key)
