"""Which original messages each adapter turn received, and a barrier to hold one open.

Reply counts cannot show how many model turns ran or which messages shared one, so
this observes the turns themselves. ``InvocationObserver.watch`` wraps ``on_turn`` of
the real adapter instance a cell built. A singleton (``on_message``) and a batch
(``on_messages``) both reach the model through that one model-input builder, so it is
the lowest boundary every batch-capable adapter shares. The wrapper records the
turn's message ids, optionally parks it, then calls the real method with the same
arguments: admission, judgement and settlement run exactly as in production, and
platform delivery is never replaced.

A ``Hold`` parks one turn at its entry. By then the room has already admitted the
turn's originals, so everything the test sends while it is parked queues behind it:
the deterministic stand-in for "messages arrive while the agent is busy", with no
sleeps and no timing windows.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from band.core.exceptions import InputNotAccepted
from band.core.simple_adapter import SimpleAdapter
from band.runtime.messages import PlatformMessage

logger = logging.getLogger(__name__)


@dataclass
class Invocation:
    """One adapter turn: the room and the original message ids it was handed, oldest first.

    ``refused`` marks an attempt the adapter turned away with ``InputNotAccepted``
    (a busy room): no provider execution started, so it was not a turn.
    """

    room_id: str
    message_ids: tuple[str, ...]
    refused: bool = False


class Hold:
    """Parks one invocation at its entry until released."""

    def __init__(self, *, deadline_s: float) -> None:
        self._deadline_s = deadline_s
        self._entered = asyncio.Event()
        self._released = asyncio.Event()
        self._invocation: Invocation | None = None

    async def entered(self, *, deadline_s: float | None = None) -> Invocation:
        """Block until an invocation is parked here; return it.

        The deadline is a failure bound only: it names the hold that never filled
        instead of leaving the test to the hard pytest timeout.
        """
        deadline = self._deadline_s if deadline_s is None else deadline_s
        try:
            await asyncio.wait_for(self._entered.wait(), timeout=deadline)
        except TimeoutError:
            raise TimeoutError(
                f"no adapter invocation reached the hold within {deadline:.0f}s"
            ) from None
        assert self._invocation is not None  # set before ``_entered``
        return self._invocation

    def release(self) -> None:
        self._released.set()

    async def park(self, invocation: Invocation) -> None:
        self._invocation = invocation
        self._entered.set()
        await self._released.wait()


class InvocationObserver:
    """Records every turn of the adapters it watches and holds chosen ones open."""

    def __init__(self, *, deadline_s: float) -> None:
        self._deadline_s = deadline_s
        self._invocations: list[Invocation] = []
        self._armed: defaultdict[str, deque[Hold]] = defaultdict(deque)
        self._parked: list[Hold] = []
        self._released = False

    def watch(self, adapter: SimpleAdapter[Any]) -> None:
        """Record every ``on_turn`` of ``adapter``, then run its real ``on_turn``."""
        real_on_turn = adapter.on_turn

        async def observed_on_turn(
            messages: Sequence[PlatformMessage], *args: Any, room_id: str, **kwargs: Any
        ) -> None:
            invocation = Invocation(
                room_id=room_id,
                message_ids=tuple(message.id for message in messages),
            )
            await self._enter(invocation)
            try:
                await real_on_turn(messages, *args, room_id=room_id, **kwargs)
            except InputNotAccepted:
                invocation.refused = True
                raise

        setattr(adapter, "on_turn", observed_on_turn)  # noqa: B010 -- an instance-level wrap; the class is untouched

    async def _enter(self, invocation: Invocation) -> None:
        logger.info(
            "Adapter turn %d in room %s over messages %s",
            len(self._invocations) + 1,
            invocation.room_id,
            list(invocation.message_ids),
        )
        self._invocations.append(invocation)
        armed = self._armed[invocation.room_id]
        if armed and not self._released:
            hold = armed.popleft()
            self._parked.append(hold)
            await hold.park(invocation)

    def hold_next(self, room_id: str) -> Hold:
        """Park the next invocation to start in ``room_id``; holds fill in arming order."""
        hold = Hold(deadline_s=self._deadline_s)
        self._armed[room_id].append(hold)
        return hold

    def partition(self, room_id: str) -> list[tuple[str, ...]]:
        """The message ids of each turn in ``room_id``, in the order they started.

        An attempt the adapter refused as busy is not a turn and is left out.
        """
        return [
            invocation.message_ids
            for invocation in self._invocations
            if invocation.room_id == room_id and not invocation.refused
        ]

    def handed(self, room_id: str) -> list[str]:
        """Every original the turns in ``room_id`` were handed, oldest turn first.

        An original appears once per turn that ran it, so a repeat means the model
        ran it again.
        """
        return [
            message_id
            for message_ids in self.partition(room_id)
            for message_id in message_ids
        ]

    def release_all(self) -> None:
        """Free every parked invocation and park no later one, so a failed test can stop its agent."""
        self._released = True
        for hold in self._parked:
            hold.release()
