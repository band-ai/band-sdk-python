"""Shared live decline checks."""

from __future__ import annotations

from band.core.types import MessageType
from band.runtime.tools import BandTool
from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.provisioning import ProvisionedAgent


async def assert_declined(
    capture: ReplyCapture,
    message_id: str,
    recipient: ProvisionedAgent,
) -> None:
    """The recipient processes a message through no_reply, without text or errors."""
    await capture.wait_for_processed(message_id, recipient.id)
    calls = await capture.tool_calls(sender_id=recipient.id)
    messages = await capture.events(MessageType.TEXT, sender_id=recipient.id)
    errors = await capture.errors(sender_id=recipient.id)
    calls.assert_fired(BandTool.NO_REPLY)
    messages.assert_none()
    errors.assert_none()
