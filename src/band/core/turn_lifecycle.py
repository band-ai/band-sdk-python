"""Shared ``on_interrupt`` for adapters whose manual-approval turn runs detached.

``ExecutionContext.interrupt()``/``stop_room()`` only cancel the task that
invoked ``on_message`` -- once that returns early because a turn released the
room to await a human's approval reply, only the adapter itself can still
abort the detached turn. See ``SimpleAdapter.on_interrupt``.
"""

from __future__ import annotations

from band.client.streaming import ControlMode


class ApprovalInterruptMixin:
    """``on_interrupt`` for a detached turn that can be hard-cancelled: decline
    the room's pending approval, then cancel the turn.

    Requires ``_clear_pending_approvals_for_room`` and ``_cancel_turn`` (see
    ``ClaudeSDKAdapter``, ``CodexAdapter``) -- both already named identically
    on the two adapters this mixes into, so the interrupt behavior is one
    definition instead of two copies.
    """

    def _clear_pending_approvals_for_room(self, room_id: str) -> None: ...

    async def _cancel_turn(self, room_id: str) -> None: ...

    async def on_interrupt(self, room_id: str, mode: ControlMode) -> None:
        self._clear_pending_approvals_for_room(room_id)
        await self._cancel_turn(room_id)
