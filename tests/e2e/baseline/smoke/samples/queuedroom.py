"""Room driver for the queued-message smokes: one agent, its senders, one watched room."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from band.client.streaming import DeliveryStatus
from band.core.types import MessageType
from band.runtime.types import SessionConfig
from tests.e2e.baseline.smoke.samples.sample_agents import (
    liveness_probe,
    unique_marker,
)
from tests.e2e.baseline.timeouts import SlowTurnBudget
from tests.e2e.baseline.toolkit.capture import CaptureFactory, ReplyCapture
from tests.e2e.baseline.toolkit.invocations import Hold, InvocationObserver
from tests.e2e.baseline.toolkit.observations import Events, Replies
from tests.e2e.baseline.toolkit.provisioning import AdapterCell, ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps

# Requests queued behind the held opener, and the sequential turn barriers a run
# can await: the opener, the next held turn, then every queued request and the
# later one (one turn each when batching is off).
QUEUED_COUNT = 3
TURN_BARRIERS = QUEUED_COUNT + 3


class MessageSender(Protocol):
    """What ``UserOps`` (a human) and ``PeerActor`` (an agent) share: post one message mentioning the agent."""

    async def send_message(
        self, room_id: str, content: str, *, mention_id: str, mention_name: str
    ) -> str: ...


@dataclass(frozen=True)
class Sender:
    """A participant who posts to the agent: a human via ``UserOps`` or a peer agent."""

    post: MessageSender
    id: str


@dataclass(frozen=True)
class QueuedRun:
    """What one run of ``QueuedRoom.queue_while_busy`` produced.

    ``admitted`` is read from the platform while the first turn after the opener is
    held open: the ids among ``queued`` and ``later`` the agent had begun processing.
    ``replies`` are the agent's replies from after the opener's own reply onward.
    """

    opener: str
    queued: list[str]
    later: str
    markers: list[str]
    admitted: list[str]
    replies: Replies
    processed: list[str]

    @property
    def originals(self) -> list[str]:
        return [self.opener, *self.queued, self.later]

    @property
    def batched_turns(self) -> list[tuple[str, ...]]:
        """The turns of a run that handed the queued originals over together."""
        return [(self.opener,), tuple(self.queued), (self.later,)]

    @property
    def singleton_turns(self) -> list[tuple[str, ...]]:
        """The turns of a run that handed every original over alone."""
        return [(message_id,) for message_id in self.originals]

    def assert_every_request_answered(self) -> None:
        for marker in self.markers:
            self.replies.assert_contains_any([marker])


@dataclass(frozen=True)
class QueuedBatch:
    """What one run of ``QueuedRoom.queue_texts_while_busy`` produced.

    ``replies`` are the agent's chat messages after the opener's own reply, read
    from the platform once every queued original reached a final state;
    ``processed`` are the originals the agent had processed by then.
    """

    opener: str
    queued: list[str]
    replies: Events
    processed: list[str]

    @property
    def originals(self) -> list[str]:
        return [self.opener, *self.queued]

    @property
    def batched_turns(self) -> list[tuple[str, ...]]:
        """The turns of a run that handed the queued originals over together."""
        return [(self.opener,), tuple(self.queued)]


@dataclass(frozen=True)
class QueuedRoom:
    """A running agent in a room with its senders (the owner first), whose turns are observed."""

    agent: ProvisionedAgent
    room_id: str
    capture: ReplyCapture
    observer: InvocationObserver
    senders: Sequence[Sender]
    budget: SlowTurnBudget

    @property
    def owner(self) -> Sender:
        return self.senders[0]

    @property
    def partition(self) -> list[tuple[str, ...]]:
        """The message ids each turn in this room was handed, in the order the turns ran."""
        return self.observer.partition(self.room_id)

    def hold_next(self) -> Hold:
        return self.observer.hold_next(self.room_id)

    async def say(self, sender: Sender, text: str) -> str:
        """Post ``text`` to the agent as ``sender``; return the message id."""
        return await sender.post.send_message(
            self.room_id,
            text,
            mention_id=self.agent.id,
            mention_name=self.agent.name,
        )

    async def ask(self, sender: Sender, marker: str) -> str:
        """Post a request for the agent to confirm ``marker`` as ``sender``; return the message id."""
        return await self.say(sender, liveness_probe(marker))

    async def queue_while_busy(self, senders: Sequence[Sender]) -> QueuedRun:
        """Queue one request per sender behind a held opener turn, send one more while
        the first turn after the opener is held open, then let everything finish."""
        deadline_s = self.budget.deadline_s
        markers = [unique_marker(f"queued{index}") for index in range(len(senders))]
        opener, opener_hold = await self._park_opener()

        queued = [
            await self.ask(sender, marker) for sender, marker in zip(senders, markers)
        ]
        queued_hold = self.hold_next()
        after_opener = await self._release_opener(opener, opener_hold)

        await queued_hold.entered(deadline_s=deadline_s)
        later = await self.ask(self.owner, unique_marker("later"))
        admitted = await self._with_status([*queued, later], DeliveryStatus.PROCESSING)
        queued_hold.release()
        replies = await self.capture.wait_for_reply(
            later,
            self.agent.id,
            since=after_opener,
            deadline_s=deadline_s * (len(queued) + 1),
        )
        processed = await self._with_status(
            [opener, *queued, later], DeliveryStatus.PROCESSED
        )
        return QueuedRun(
            opener=opener,
            queued=queued,
            later=later,
            markers=markers,
            admitted=admitted,
            replies=replies,
            processed=processed,
        )

    async def queue_texts_while_busy(self, texts: Sequence[str]) -> QueuedBatch:
        """Post ``texts`` as the owner behind a held opener turn, release it, and wait
        for the agent to finish every one of them, answered or not."""
        opener, opener_hold = await self._park_opener()
        queued = [await self.say(self.owner, text) for text in texts]
        after_opener = await self._release_opener(opener, opener_hold)
        await self.capture.wait_for_delivery(
            queued[-1],
            self.agent.id,
            until={DeliveryStatus.PROCESSED, DeliveryStatus.FAILED},
            deadline_s=self.budget.deadline_s,
        )
        return QueuedBatch(
            opener=opener,
            queued=queued,
            replies=await self._replies_since(after_opener),
            processed=await self._with_status(
                [opener, *queued], DeliveryStatus.PROCESSED
            ),
        )

    async def _park_opener(self) -> tuple[str, Hold]:
        """Send the opener and return it once its turn is parked, so the room stays committed to it."""
        hold = self.hold_next()
        opener = await self.ask(self.owner, unique_marker("opener"))
        await hold.entered()
        return opener, hold

    async def _release_opener(self, opener: str, hold: Hold) -> int:
        """Let the opener finish; return the capture cursor just past its reply."""
        hold.release()
        await self.capture.wait_for_reply(
            opener, self.agent.id, deadline_s=self.budget.deadline_s
        )
        return self.capture.messages.snapshot()

    async def _replies_since(self, cursor: int) -> Events:
        """The agent's chat messages other than the ones captured before ``cursor``.

        Read from the platform, not the live capture: a reply frame and the
        delivery-status frame arrive unordered, so only a durable read after the
        final status proves a turn posted nothing.
        """
        earlier = [message.id for message in self.capture.messages[:cursor]]
        texts = await self.capture.events(MessageType.TEXT, sender_id=self.agent.id)
        return texts.excluding(*earlier)

    async def _with_status(
        self, message_ids: list[str], status: DeliveryStatus
    ) -> list[str]:
        """The ids among ``message_ids`` at ``status`` for the agent, read durably from the platform."""
        statuses = await self.capture.durable_delivery_statuses(
            message_ids, self.agent.id
        )
        return [mid for mid in message_ids if statuses[mid] is status]


@asynccontextmanager
async def queued_room(
    cell: AdapterCell,
    *,
    owner: UserOps,
    reply_capture: CaptureFactory,
    observer: InvocationObserver,
    budget: SlowTurnBudget,
    consume_all_queued: bool,
    guests: Sequence[UserOps] = (),
    peers: Sequence[ProvisionedAgent] = (),
) -> AsyncIterator[QueuedRoom]:
    """Run the cell's agent under ``consume_all_queued`` in a fresh room shared with
    ``guests`` and ``peers``, observed from before the first message.

    Everyone joins before the agent's first turn, so no roster change lands among the
    queued messages.
    """
    async with AsyncExitStack() as stack:
        agent = await stack.enter_async_context(
            cell.running(
                session_config=SessionConfig(consume_all_queued=consume_all_queued),
                observer=observer,
            )
        )
        guest_ids = [
            await stack.enter_async_context(owner.contact_with(guest))
            for guest in guests
        ]
        room_id = await cell.resources.provision_room(
            title=f"e2e-queued-{cell.adapter_id}",
            participants=[agent.id, *guest_ids, *(peer.id for peer in peers)],
        )
        capture = await stack.enter_async_context(reply_capture(room_id))
        senders = [
            Sender(post=owner, id=await owner.whoami()),
            *(Sender(post=ops, id=user_id) for ops, user_id in zip(guests, guest_ids)),
            *(Sender(post=cell.resources.peer(peer), id=peer.id) for peer in peers),
        ]
        yield QueuedRoom(
            agent=agent,
            room_id=room_id,
            capture=capture,
            observer=observer,
            senders=senders,
            budget=budget,
        )
