"""Admit immutable reviews and consume each confirmation at most once."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from bandteams.evidence import Claim, Delivery, Journal, Receipt
from bandteams.settings import CONFIRM_SECONDS, REVIEW_BYTES, Binding, StrictModel

logger = logging.getLogger(__name__)


class Origin(StrictModel):
    """Carry only authenticated Teams envelope correlation."""

    tenant: str
    actor: str
    team: str
    channel: str
    conversation: str
    activity: str


class Refused(ValueError):
    """Reject a stale or unauthorized action without dispatch."""


class Proposals:
    """Own memory-only bodies and metadata-only durable admission."""

    def __init__(
        self, journal: Journal, *, clock: Callable[[], float] = time.time
    ) -> None:
        self.clock = clock
        self.journal = journal
        self.claims = journal.recover()
        self.bodies: dict[str, str] = {}
        self.generations: dict[str, str] = {}

    def activate(self, person: Binding) -> str:
        generation = str(uuid4())
        self.retire(person.alias)
        self.generations[person.alias] = generation
        return generation

    def retire(self, alias: str) -> None:
        """Fence actions synchronously before cancellation can yield."""
        self.generations.pop(alias, None)
        for claim in tuple(self.claims.values()):
            if claim.alias == alias:
                if claim.delivery == Delivery.REVIEW:
                    self.transition(claim, delivery=Delivery.CANCELLED)
                elif claim.delivery == Delivery.PENDING:
                    self.transition(claim, delivery=Delivery.FAILED)
                self.bodies.pop(claim.proposal_id, None)

    def transition(self, claim: Claim, **changes: Any) -> Claim:
        updated = claim.model_copy(update=changes)
        self.journal.claim(updated)
        self.claims[claim.proposal_id] = updated
        if updated.delivery not in (
            Delivery.REVIEW,
            Delivery.PENDING,
            Delivery.SENDING,
        ):
            self.bodies.pop(claim.proposal_id, None)
        return updated

    def stage(
        self, person: Binding, room: str, recipient: str, origin: Origin, body: str
    ) -> Claim:
        if not body.strip() or len(body.encode("utf-16-le")) > REVIEW_BYTES:
            raise Refused("Reply is empty or exceeds the review envelope")
        if (origin.tenant, origin.actor, origin.team, origin.channel) != (
            person.tenant_id,
            person.actor_id,
            person.team_id,
            person.channel_id,
        ):
            raise Refused("Foreign reply destination")
        if person.alias not in self.generations:
            raise Refused("Destination is not Ready")
        if any(
            (old.tenant, old.actor, old.conversation, old.source)
            == (origin.tenant, origin.actor, origin.conversation, origin.activity)
            for old in self.claims.values()
        ):
            raise Refused("Original reply was already received")
        fixture = next(
            (fixture for fixture in person.rooms if fixture.id == room), None
        )
        if fixture is None or recipient not in fixture.recipients:
            raise Refused("Recipient is outside the supplied fixture")
        for old in tuple(self.claims.values()):
            self.expire(old.proposal_id)
        if any(
            old.alias == person.alias
            and old.room == room
            and old.delivery in (Delivery.REVIEW, Delivery.PENDING, Delivery.SENDING)
            for old in self.claims.values()
        ):
            raise Refused("Finish the existing proposal, then resubmit")
        claim = Claim(
            proposal_id=str(uuid4()),
            tenant=origin.tenant,
            actor=origin.actor,
            alias=person.alias,
            band_user=person.band_user_id,
            room=room,
            generation=self.generations[person.alias],
            team=origin.team,
            channel=origin.channel,
            conversation=origin.conversation,
            source=origin.activity,
            recipient=recipient,
            expires_at=self.clock() + CONFIRM_SECONDS,
        )
        self.journal.claim(claim)
        self.claims[claim.proposal_id] = claim
        self.bodies[claim.proposal_id] = body
        return claim

    def arm(self, proposal: str, review_id: str) -> Claim:
        if not review_id.strip():
            raise Refused("Review creation outcome is unknown")
        claim = self.claims[proposal]
        if claim.delivery != Delivery.REVIEW or claim.alias not in self.generations:
            raise Refused("Review is no longer active")
        return self.transition(claim, review_id=review_id)

    def expire(self, proposal: str) -> None:
        claim = self.claims[proposal]
        if claim.delivery == Delivery.REVIEW and self.clock() >= claim.expires_at:
            self.transition(claim, delivery=Delivery.EXPIRED)

    def validate(self, proposal: str, version: int, origin: Origin) -> Claim:
        claim = self.claims.get(proposal)
        if claim is None:
            raise Refused("Unknown proposal")
        if (
            origin.tenant,
            origin.actor,
            origin.team,
            origin.channel,
            origin.conversation,
            origin.activity,
        ) != (
            claim.tenant,
            claim.actor,
            claim.team,
            claim.channel,
            claim.conversation,
            claim.review_id,
        ):
            raise Refused(
                "Action does not belong to this actor, thread and review card"
            )
        if (
            not claim.review_id
            or claim.version != version
            or self.generations.get(claim.alias) != claim.generation
        ):
            raise Refused("Review is stale")
        self.expire(proposal)
        return self.claims[proposal]

    def consume(self, proposal: str, version: int, origin: Origin) -> Claim:
        claim = self.validate(proposal, version, origin)
        if claim.delivery != Delivery.REVIEW:
            return claim
        if proposal not in self.bodies:
            raise Refused("Review body is unavailable")
        return self.transition(claim, delivery=Delivery.PENDING)

    def cancel(self, proposal: str, version: int, origin: Origin) -> Claim:
        claim = self.validate(proposal, version, origin)
        if claim.delivery == Delivery.REVIEW:
            return self.transition(claim, delivery=Delivery.CANCELLED)
        return claim


class Rejected(Exception):
    """Represent a definite Band rejection without a commit."""


class Dispatcher:
    """Own bounded background sends independently of invoke acknowledgements."""

    def __init__(
        self,
        proposals: Proposals,
        check: Callable[[Claim], Awaitable[None]],
        send: Callable[[Claim, str], Awaitable[str]],
        receipt: Callable[[Claim], Awaitable[None]],
    ) -> None:
        self.proposals = proposals
        self.check = check
        self.send = send
        self.receipt = receipt
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def confirm(self, proposal: str, version: int, origin: Origin) -> Claim:
        claim = self.proposals.validate(proposal, version, origin)
        if claim.delivery != Delivery.REVIEW:
            return claim
        claim = self.proposals.consume(proposal, version, origin)
        if claim.delivery == Delivery.PENDING and proposal not in self.tasks:
            self.tasks[proposal] = asyncio.create_task(self.run(proposal))
        return claim

    async def notify(self, proposal: str) -> None:
        claim = self.proposals.claims[proposal]
        try:
            await self.receipt(claim)
        except Exception:  # noqa: BLE001 - receipt failure must never replay the Band write
            logger.warning("Teams outcome receipt failed")
            self.proposals.transition(
                self.proposals.claims[proposal], receipt=Receipt.FAILED
            )
        else:
            self.proposals.transition(
                self.proposals.claims[proposal], receipt=Receipt.DELIVERED
            )

    async def run(self, proposal: str) -> None:
        claim = self.proposals.claims[proposal]
        lock = self.locks.setdefault(f"{claim.alias}:{claim.room}", asyncio.Lock())
        try:
            async with lock:
                await self.check(claim)
                if self.proposals.generations.get(claim.alias) != claim.generation:
                    raise Refused("Link retired before dispatch")
                body = self.proposals.bodies[proposal]
                claim = self.proposals.transition(claim, delivery=Delivery.SENDING)
                try:
                    message_id = await self.send(claim, body)
                    UUID(message_id)
                    claim = self.proposals.transition(
                        claim, delivery=Delivery.SENT, band_id=message_id
                    )
                except Rejected:
                    claim = self.proposals.transition(claim, delivery=Delivery.FAILED)
                except (Exception, asyncio.CancelledError):  # noqa: BLE001 - interrupted dispatch is unknown
                    logger.warning("Band delivery outcome is unknown")
                    claim = self.proposals.transition(claim, delivery=Delivery.UNKNOWN)
                await self.notify(proposal)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - fail closed before dispatch
            logger.warning("Confirmed dispatch stopped before completion")
            claim = self.proposals.claims[proposal]
            if claim.delivery == Delivery.PENDING:
                self.proposals.transition(claim, delivery=Delivery.FAILED)
        finally:
            self.proposals.bodies.pop(proposal, None)
            self.tasks.pop(proposal, None)

    async def stop(self, alias: str) -> None:
        self.proposals.retire(alias)
        tasks = [
            task
            for key, task in self.tasks.items()
            if self.proposals.claims[key].alias == alias
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
