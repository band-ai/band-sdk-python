"""One turn's outcome, judged by band-sdk-core's turn-outcome rule.

A turn completes when it replied, declined via ``band_no_reply``, did real work,
was settled by the adapter itself, or already reported a failure. Anything else
is a missing reply, reported once and marked FAILED. The rule and its text live
in core so every SDK judges a turn the same way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import band_sdk_core
from band_sdk_core import AgentFailure, TurnLedger, TurnVerdict

from band.core.protocols import TURN_FAILURE_PROVIDER

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol
    from band.runtime.tools.types import TurnEffect

logger = logging.getLogger(__name__)


class Turn:
    """The ledger of what one turn's tool calls did.

    ``judged`` is set by ``SimpleAdapter.run_judged_turn`` for a turn it will
    judge. ``detach()`` marks a turn released early (parked on a human
    decision); a judged one is then judged at the adapter's real end of it.
    """

    def __init__(self, *, posts_missing_reply: bool = True) -> None:
        self._ledger = TurnLedger()
        # SessionConfig.report_turn_failures_to_room: the missing reply is a
        # failure the runtime detects, so the session's opt-out covers it.
        self.posts_missing_reply = posts_missing_reply
        self.judged = False
        self._detached = False
        self._reply_attempted = False

    def record(self, effect: TurnEffect) -> None:
        core_effect = band_sdk_core.TurnEffect.from_wire_name(effect)
        if core_effect is None:
            raise ValueError(f"band-sdk-core has no turn effect {effect!r}")
        self._ledger.record(core_effect)

    def settle(self) -> None:
        """The adapter ended this turn itself (a control reply, a busy notice,
        or closing text reported as a thought under ``LeftoverText.THOUGHT``)."""
        self._ledger.settle()

    def note_reported(self) -> None:
        """A failure for this turn already reached the room."""
        self._ledger.note_reported()

    def note_reply_attempt(self) -> None:
        """A reply tool was called, whether or not its post landed."""
        self._reply_attempted = True

    def detach(self) -> None:
        """Release the turn before it ends; its delivery is already settled."""
        self._detached = True

    @property
    def detached(self) -> bool:
        return self._detached

    @property
    def replied(self) -> bool:
        """The turn replied or declined, so the model's final text is not relayed."""
        return self._ledger.reply_settled()

    @property
    def reply_attempted(self) -> bool:
        """A reply tool was called this turn. Unless the turn also ``replied``,
        that reply did not land (refused, failed, denied or unfinished), and
        text the model wrote around it may describe a reply the room never
        received."""
        return self._reply_attempted

    @property
    def complete(self) -> bool:
        return self._ledger.verdict() == TurnVerdict.Complete


#: What a turn that ended without completing reports to the room.
MISSING_REPLY = AgentFailure(
    TURN_FAILURE_PROVIDER, band_sdk_core.missing_reply_message()
)


async def report_unsettled_turn(tools: AgentToolsProtocol, *, room_id: str) -> bool:
    """Report a turn that ended without completing; return whether it did.

    The one place a missing-reply verdict becomes a room-visible failure.
    """
    if tools.turn.complete:
        return False
    logger.warning(
        "Room %s: turn ended without a reply (detached=%s)",
        room_id,
        tools.turn.detached,
    )
    if tools.turn.posts_missing_reply:
        await tools.send_failure(MISSING_REPLY)
    return True


async def judge_detached_turn(tools: AgentToolsProtocol, *, room_id: str) -> None:
    """Judge a judged, detached turn at the adapter's real end of it.

    ``run_judged_turn`` returned before such a turn ended; an attached turn was
    already judged there, and an unjudged one is never reported.
    """
    if tools.turn.judged and tools.turn.detached:
        await report_unsettled_turn(tools, room_id=room_id)
