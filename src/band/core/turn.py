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

    ``judged`` is set by ``SimpleAdapter.on_event`` for a turn it will judge.
    ``detach()`` marks a turn released early (parked on a human decision), so
    the adapter judges it at its real end instead of ``on_event``.
    """

    def __init__(self) -> None:
        self._ledger = TurnLedger()
        self.judged = False
        self._detached = False

    def record(self, effect: TurnEffect) -> None:
        core_effect = band_sdk_core.TurnEffect.from_wire_name(effect)
        if core_effect is None:
            raise ValueError(f"band-sdk-core has no turn effect {effect!r}")
        self._ledger.record(core_effect)

    def settle(self) -> None:
        """The adapter ended this turn itself (a control reply, a busy notice)."""
        self._ledger.settle()

    def note_reported(self) -> None:
        """A failure for this turn already reached the room."""
        self._ledger.note_reported()

    def detach(self) -> None:
        """Move a judged turn's verdict to the adapter's real end of the turn.

        A turn ``on_event`` does not judge stays attached, so the adapter's
        ``if turn.detached`` report never fires for it.
        """
        if self.judged:
            self._detached = True

    @property
    def detached(self) -> bool:
        return self._detached

    @property
    def replied(self) -> bool:
        """The turn replied or declined, so the model's final text is not relayed."""
        return self._ledger.reply_settled()

    @property
    def complete(self) -> bool:
        return self._ledger.verdict() == TurnVerdict.Complete


async def report_unsettled_turn(tools: AgentToolsProtocol) -> bool:
    """Report a turn that ended without completing; return whether it did.

    The one place a missing-reply verdict becomes a room-visible failure.
    """
    if tools.turn.complete:
        return False
    await tools.send_failure(
        AgentFailure(TURN_FAILURE_PROVIDER, band_sdk_core.missing_reply_message())
    )
    return True
