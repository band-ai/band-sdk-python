"""Bounded inter-agent ask-and-relay exchange.

The driver seeds one message in a shared room, @mentioning only the asker.
Band only delivers messages to mentioned agents, so the marker inside that
seed is invisible to the responder. An ordered chain of marker-bearing
replies then proves the hand-off occurred. Handoff replies must carry Band's
structured mention metadata; text that merely looks like ``@someone`` is not
enough to prove target routing.

Bounds, whichever hits first (the wait stops immediately on success):
  - a wall-clock deadline (~90s)
  - a turn cap (6 agent messages, never open-ended) — enforced inside the
    wait predicate, so a chatty runaway fails fast instead of timing out.

The marker embeds the run id, so a stale message from any earlier run can
never satisfy the predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations import Replies
from tests.e2e.baseline.toolkit.user_ops import UserOps

MAX_AGENT_MESSAGES = 6
DEADLINE_S = 90.0

_SEED = (
    "Ask @{responder_handle} to reply in this room with the agreed marker "
    "{token}. Once they reply, relay that marker back to this room in one final "
    "message, then stop. Do not answer on their behalf."
)


class ExchangeAgent(Protocol):
    """Minimal agent identity for exchange steps."""

    id: str
    name: str


def marker(run_id: str, *, prefix: str = "E2E-MARK") -> str:
    """A deterministic, run-scoped token: stale messages from earlier runs
    can never satisfy a predicate looking for it."""
    return f"{prefix}-{run_id.upper()}"


def _said(message, sender_id: str, token: str) -> bool:
    return message.sender_id == sender_id and token.lower() in (
        message.content or ""
    ).lower()


@dataclass(frozen=True)
class MentionChainStep:
    """One required message in an ordered inter-agent exchange.

    A recipient means the sender must use that participant's structured Band
    mention. A recipient-less step is a response or relay, where authorship,
    ordering, and the run-scoped marker are the observable proof.
    """

    sender: ExchangeAgent
    recipient: ExchangeAgent | None = None
    recipient_handle: str | None = None

    def matches(self, message, token: str) -> bool:
        if not _said(message, self.sender.id, token):
            return False
        if self.recipient is None:
            return True
        metadata = message.metadata
        return bool(
            metadata
            and any(mention.id == self.recipient.id for mention in metadata.mentions)
        )

    def describe(self, token: str) -> str:
        target = (
            f" mentioning @{self.recipient_handle}"
            if self.recipient is not None
            else ""
        )
        return f"a reply from {self.sender.name}{target} containing {token}"


def chain_completed(
    steps: tuple[MentionChainStep, ...], token: str, messages: list
) -> bool:
    """Whether ``messages`` contain the steps as an ordered subsequence: each
    step matched at or after the previous match, gaps allowed. The wait
    predicate and the outcome share this one definition of "done"."""
    cursor = 0
    for step in steps:
        index = next(
            (
                index
                for index, message in enumerate(messages[cursor:], cursor)
                if step.matches(message, token)
            ),
            None,
        )
        if index is None:
            return False
        cursor = index + 1
    return True


@dataclass(frozen=True)
class MentionChainOutcome:
    """Captured transcript evaluated against a declarative mention chain."""

    token: str
    steps: tuple[MentionChainStep, ...]
    transcript: Replies
    max_messages: int

    def is_completed(self, messages: list) -> bool:
        return chain_completed(self.steps, self.token, messages)

    def assert_completed(self) -> None:
        expected = " → ".join(step.describe(self.token) for step in self.steps)
        assert self.is_completed(self.transcript), (
            f"expected ordered chain: {expected}; "
            f"captured {len(self.transcript)} agent message(s)"
        )

    def assert_bounded(self) -> None:
        assert len(self.transcript) <= self.max_messages, (
            f"expected at most {self.max_messages} agent messages, "
            f"got {len(self.transcript)} — a runaway exchange"
        )


async def run_exchange(
    *,
    capture: ReplyCapture,
    user_ops: UserOps,
    room_id: str,
    asker: ExchangeAgent,
    asker_mention_name: str,
    responder: ExchangeAgent,
    responder_handle: str,
    token: str,
    deadline_s: float = DEADLINE_S,
) -> MentionChainOutcome:
    """Seed the exchange and wait until it succeeds or hits a bound.

    Returns a ``MentionChainOutcome`` either way — asserting is the test's job.
    The capture must already be open on ``room_id`` (subscribe-before-send).
    """
    steps = (
        MentionChainStep(asker, responder, responder_handle),
        MentionChainStep(responder),
        MentionChainStep(asker),
    )

    await user_ops.send_message(
        room_id,
        _SEED.format(responder_handle=responder_handle, token=token),
        mention_id=asker.id,
        mention_name=asker_mention_name,
    )

    try:
        await capture.wait_until(
            lambda messages: chain_completed(steps, token, messages)
            or len(messages) >= MAX_AGENT_MESSAGES,
            deadline_s=deadline_s,
        )
    except TimeoutError:
        pass  # whatever was captured is returned; asserting is the test's job

    return MentionChainOutcome(
        token=token,
        steps=steps,
        transcript=Replies(capture.messages),
        max_messages=MAX_AGENT_MESSAGES,
    )
