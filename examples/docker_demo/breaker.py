"""Conversation circuit breaker for the Docker demo.

Three autonomous LLM agents talk by @mentioning each other. Left alone they can
ping-pong forever, so the demo needs a mechanical stop that does not depend on
any model choosing to be well-behaved. This module is that stop, kept as a pure
state machine — no Band, no network, no clock — so every tier can be exercised
offline against a scripted message stream (see tests/example_agents/).

It tracks three *distinct* facts about the meeting rather than collapsing them
into one flag (each proven or falsified independently):

  * ``handoff_requested`` — the PM @mentioned the Architect (intent to hand off).
  * ``architect_present``  — the Architect actually joined / spoke (Act 2 is real).
  * ``decision``           — the Architect posted an explicit verdict (the end).

A mere @mention (intent) does NOT prove the Architect will reply, so it never
suppresses the add-fallback — only ``architect_present`` does. And only a message
carrying an explicit decision marker ends the meeting — an "I'm reviewing this"
must not start the grace timer.

Tiers, evaluated on every ``poll``:
  1. terminate — a decision was seen and the grace window elapsed (happy path).
  2. hard-kill — no decision yet and ``hard_cap`` agent messages or ``wall_clock_s``
                 elapsed. A meeting that reached a decision is never hard-killed;
                 its tail is bounded by ``grace_s`` (so total <= wall_clock_s + grace_s).
  3. nudge     — soft_cap design messages, no handoff requested yet: nudge the PM.
  4. add       — handoff_deadline further messages with the Architect still absent:
                 add it ourselves (the PM's invite never landed).

Counting: only agent messages move the caps. Human/conductor messages are
recorded but never counted — interjecting must not trip the breaker.
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
    ADD_ARCHITECT = "add_architect"  # PM never got the Architect in — add it ourselves
    HARD_KILL = "hard_kill"  # force-terminate: runaway or timeout
    TERMINATE_OK = "terminate_ok"  # clean end: Architect decided, grace elapsed


@dataclass(frozen=True)
class ObservedMessage:
    """A single text message the conductor saw in the room.

    ``timestamp`` is epoch seconds (used only for ordering context, never for the
    breaker's own timers — those run on the conductor clock passed to ``poll``).
    ``mentions_architect`` marks a handoff *request*; ``is_final_decision`` marks
    an Architect message that carries an explicit verdict (the only thing that
    ends the meeting).
    """

    sender_class: SenderClass
    timestamp: float
    mentions_architect: bool = False
    is_final_decision: bool = False


@dataclass(frozen=True)
class BreakerConfig:
    """Tunable ceilings. All env-overridable so they can be adjusted before a show."""

    soft_cap: int = 6  # PM<->Dev design messages before we nudge a handoff
    handoff_deadline: int = (
        2  # further design messages, Architect still absent, before we add it
    )
    hard_cap: int = 12  # total agent messages before we force-kill
    wall_clock_s: float = (
        300.0  # ceiling for reaching a decision (tail bounded by grace_s)
    )
    grace_s: float = 20.0  # wait after the decision before stopping


class CircuitBreaker:
    """Message-driven state machine bounding a three-agent design meeting.

    Feed it every text message with ``record`` and ask ``poll(now)`` for the
    actions due; ``now`` is the conductor's own clock, and the breaker times both
    the wall-clock and the post-decision grace against it (never the platform's
    message timestamps), so clock skew can't skew the timers. Use it as a guard:
    ``with breaker: ...`` closes the meeting on block exit.
    """

    def __init__(
        self, config: BreakerConfig | None = None, *, start_time: float
    ) -> None:
        self.config = config or BreakerConfig()
        self._start = start_time
        self._design_count = (
            0  # PM<->Dev messages before the Architect joins (drives soft cap)
        )
        self._agent_count = 0  # all agent messages (drives hard cap)
        self._handoff_requested = False  # PM @mentioned the Architect
        self._architect_present = False  # Architect joined or spoke (Act 2 is real)
        self._decision_seen = False  # Architect posted an explicit verdict
        self._decision_at: float | None = (
            None  # conductor-clock time the verdict was first polled
        )
        self._nudged = False  # soft nudge already fired (fire once)
        self._added = False  # add-fallback already fired (fire once)
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
        """Conductor confirmed the Architect is a room participant — Act 2 is real,
        so the add-fallback is no longer needed."""
        self._architect_present = True

    def record(self, msg: ObservedMessage) -> None:
        """Ingest one observed text message, updating counts and the three facts."""
        if self._terminal:
            return

        if msg.mentions_architect and msg.sender_class is not SenderClass.ARCHITECT:
            self._handoff_requested = True

        if msg.sender_class not in AGENT_SENDERS:
            return  # human / conductor / unknown: recorded, never counted

        self._agent_count += 1
        if msg.sender_class in DESIGN_PAIR and not self._architect_present:
            self._design_count += 1

        if msg.sender_class is SenderClass.ARCHITECT:
            self._architect_present = True
            if msg.is_final_decision:
                self._decision_seen = True

    def poll(self, now: float) -> list[Action]:
        """Return the actions due at time ``now`` (empty once a terminal action fired)."""
        if self._terminal:
            return []

        # Stamp the decision on the conductor clock the first time we see it, so the
        # grace window is measured in the same clock as the wall-clock ceiling.
        if self._decision_seen and self._decision_at is None:
            self._decision_at = now

        if self._decision_at is not None:
            if now - self._decision_at >= self.config.grace_s:
                self._terminal = True
                logger.info("breaker: clean terminate (decision + grace elapsed)")
                return [Action.TERMINATE_OK]
            return []  # decided — hold through the grace tail, never hard-kill

        if (
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

        if not self._architect_present:
            if (
                not self._nudged
                and not self._handoff_requested
                and self._design_count >= self.config.soft_cap
            ):
                self._nudged = True
                logger.info("breaker: soft cap reached, nudging the PM to hand off")
                return [Action.NUDGE_HANDOFF]
            if (
                not self._added
                and self._design_count
                >= self.config.soft_cap + self.config.handoff_deadline
            ):
                self._added = True
                logger.info(
                    "breaker: handoff deadline passed with no Architect — adding it"
                )
                return [Action.ADD_ARCHITECT]

        return []

    @property
    def stopped(self) -> bool:
        """True once a terminal action (kill or clean terminate) has fired."""
        return self._terminal
