"""Conversation circuit breaker for the Docker demo.

Three autonomous LLM agents talk by @mentioning each other. Left alone they can
ping-pong forever, so the demo needs a mechanical stop that does not depend on
any model choosing to be well-behaved. This module is that stop, kept as a pure
state machine — no Band, no network, no clock — so every tier can be exercised
offline against a scripted message stream (see tests/examples/).

Design meeting shape the breaker guards:
  Act 1  PM and Developer discuss (text messages).
  Act 2  PM invites the Architect; the Architect posts one decision.
  End    clean stop shortly after that decision.

Tiers, evaluated on every ``poll``:
  1. terminate  — Architect has decided and the grace window elapsed (happy path).
  2. hard-kill  — total agent messages hit ``hard_cap`` or ``wall_clock_s`` elapsed.
  3. nudge      — no handoff after ``soft_cap`` design messages: push the PM to
                  hand off, and add the Architect ourselves if it never joined.

Counting rule (matters once a human presenter is in the room): only agent
messages move the caps. Human and conductor messages are recorded but never
counted — interjecting must not trip the breaker.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class SenderClass(str, Enum):
    """Who authored an observed message, from the conductor's point of view."""

    PM = "pm"
    DEVELOPER = "developer"
    ARCHITECT = "architect"
    HUMAN = "human"
    CONDUCTOR = "conductor"
    UNKNOWN = "unknown"


# The design pair whose exchange the soft cap bounds.
DESIGN_PAIR = frozenset({SenderClass.PM, SenderClass.DEVELOPER})
# Everyone whose messages move the caps.
AGENT_SENDERS = DESIGN_PAIR | {SenderClass.ARCHITECT}


class Action(str, Enum):
    """A step the conductor must take when the breaker returns it."""

    NUDGE_HANDOFF = (
        "nudge_handoff"  # post a facilitator message pushing the PM to hand off
    )
    ADD_ARCHITECT = "add_architect"  # PM never invited it — add the Architect ourselves
    HARD_KILL = "hard_kill"  # force-terminate: runaway or timeout
    TERMINATE_OK = "terminate_ok"  # clean end: Architect decided, grace elapsed


@dataclass(frozen=True)
class ObservedMessage:
    """A single text message the conductor saw in the room.

    ``timestamp`` is epoch seconds; the breaker never reads a real clock so tests
    can drive time explicitly. ``mentions_architect`` is true when the message
    @mentions the Architect — the signal that Act 1 is handing off to Act 2.
    """

    sender_class: SenderClass
    timestamp: float
    mentions_architect: bool = False


@dataclass(frozen=True)
class BreakerConfig:
    """Tunable ceilings. All env-overridable so they can be adjusted before a show."""

    soft_cap: int = 6  # PM<->Dev design messages before we nudge a handoff
    hard_cap: int = 12  # total agent messages before we force-kill
    wall_clock_s: float = 300.0  # absolute time ceiling for the whole meeting
    grace_s: float = 20.0  # wait after the Architect's decision before stopping


class CircuitBreaker:
    """Message-driven state machine bounding a three-agent design meeting.

    Feed it every text message with ``record`` and ask ``poll(now)`` for the
    actions due. It is deliberately transport-free: the conductor owns polling,
    filtering to text messages, and executing the returned actions.
    """

    def __init__(
        self, config: BreakerConfig | None = None, *, start_time: float
    ) -> None:
        self.config = config or BreakerConfig()
        self._start = start_time
        self._design_count = 0  # PM<->Dev messages before handoff (drives soft cap)
        self._agent_count = 0  # all agent messages (drives hard cap)
        self._handoff = False  # Architect invited, present, or posted -> Act 2
        self._architect_decided_at: float | None = None
        self._nudged = False  # soft nudge already fired (fire once)
        self._terminal = False  # a kill/terminate already fired (stop acting)

    def __enter__(self) -> CircuitBreaker:
        """Enter the guarded meeting. Lock-like: the caller drives ``record``/``poll``
        inside the ``with`` block, and leaving it always closes the meeting."""
        return self

    def __exit__(self, *exc: object) -> bool:
        """Closing the block terminates the breaker for good — a runaway can't
        outlive the guarded region even if the loop breaks or raises."""
        self._terminal = True
        return False  # never suppress an exception

    def note_architect_present(self) -> None:
        """Conductor detected the Architect joined the room — we're in Act 2, stop nudging."""
        self._handoff = True

    def record(self, msg: ObservedMessage) -> None:
        """Ingest one observed text message, updating counts and phase flags."""
        if self._terminal:
            return

        if msg.mentions_architect and msg.sender_class is not SenderClass.ARCHITECT:
            self._handoff = True

        if msg.sender_class not in AGENT_SENDERS:
            return  # human / conductor / unknown: recorded, never counted

        self._agent_count += 1
        if msg.sender_class in DESIGN_PAIR and not self._handoff:
            self._design_count += 1

        if msg.sender_class is SenderClass.ARCHITECT:
            self._handoff = True
            if self._architect_decided_at is None:
                self._architect_decided_at = msg.timestamp

    def poll(self, now: float) -> list[Action]:
        """Return the actions due at time ``now`` (empty once a terminal action fired)."""
        if self._terminal:
            return []

        if (
            self._architect_decided_at is not None
            and now - self._architect_decided_at >= self.config.grace_s
        ):
            self._terminal = True
            logger.info("breaker: clean terminate (architect decided, grace elapsed)")
            return [Action.TERMINATE_OK]

        # Once the Architect has decided we're committed to the clean terminate above;
        # don't let the decision message itself (which can hit the cap) trigger a kill.
        if self._architect_decided_at is None and (
            now - self._start >= self.config.wall_clock_s
            or self._agent_count >= self.config.hard_cap
        ):
            self._terminal = True
            logger.warning(
                "breaker: hard kill (agent_msgs=%d/%d, elapsed=%.0fs/%.0fs)",
                self._agent_count,
                self.config.hard_cap,
                now - self._start,
                self.config.wall_clock_s,
            )
            return [Action.HARD_KILL]

        if (
            not self._handoff
            and not self._nudged
            and self._design_count >= self.config.soft_cap
        ):
            self._nudged = True
            # No handoff means the PM never invited the Architect, so add it ourselves.
            # The conductor's add is idempotent, so pairing it with the nudge is safe.
            logger.info("breaker: soft cap reached, nudging handoff + add-fallback")
            return [Action.NUDGE_HANDOFF, Action.ADD_ARCHITECT]

        return []

    @property
    def stopped(self) -> bool:
        """True once a terminal action (kill or clean terminate) has fired."""
        return self._terminal
