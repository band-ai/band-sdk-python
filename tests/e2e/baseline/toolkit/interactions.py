"""Shared live decline and responsiveness checks."""

from __future__ import annotations

from datetime import datetime

from band.core.types import MessageType
from band.runtime.tools import BandTool
from tests.e2e.baseline.smoke.samples.sample_agents import liveness_probe, unique_marker
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations.replies import Replies
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent
from tests.e2e.baseline.toolkit.user_ops import UserOps


async def assert_declined(
    capture: ReplyCapture,
    message_id: str,
    recipient: ProvisionedAgent,
    *,
    since: datetime | None = None,
) -> None:
    """The recipient processes a message through no_reply, without text or errors."""
    await capture.wait_for_processed(message_id, recipient.id)
    calls = await capture.tool_calls(sender_id=recipient.id, since=since)
    messages = await capture.events(
        MessageType.TEXT, sender_id=recipient.id, since=since
    )
    errors = await capture.errors(sender_id=recipient.id, since=since)
    calls.assert_fired(BandTool.NO_REPLY)
    messages.assert_none()
    errors.assert_none()


async def assert_handoff_declined(
    capture: ReplyCapture,
    outgoing: Replies,
    *,
    marker: str,
    sender: ProvisionedAgent,
    recipient: ProvisionedAgent,
) -> None:
    """A marked peer-directed FYI is declined without further conversation."""
    routed = outgoing.mentioning(recipient.id)
    routed.assert_contains_exact(marker)
    handoff = next(message for message in routed if marker in message.content)
    boundary = capture.turn_boundary(handoff)
    await assert_declined(capture, handoff.id, recipient, since=boundary)
    messages = await capture.events(
        MessageType.TEXT, sender_id=sender.id, since=boundary
    )
    messages.excluding(handoff.id).assert_none()
    errors = await capture.errors(sender_id=sender.id, since=boundary)
    errors.assert_none()


async def assert_responsive(
    capture: ReplyCapture,
    user_ops: UserOps,
    agent: ProvisionedAgent,
    *,
    since: int,
    reply_ceiling: int,
) -> None:
    """A fresh probe gets a reply without a runaway since the scenario began."""
    marker = unique_marker("liveness")
    snapshot = capture.messages.snapshot()
    mid = await user_ops.send_message(
        capture.room_id,
        liveness_probe(marker),
        mention_id=agent.id,
        mention_name=agent.name,
    )
    replies = await capture.wait_for_reply(mid, agent.id, since=snapshot)
    replies.assert_contains_exact(marker)
    capture.messages.since(since).from_sender(agent.id).assert_at_most(reply_ceiling)
