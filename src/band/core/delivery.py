"""Delivery-vs-provider-failure misclassification guard.

An adapter's reply/bookkeeping post to the room (``send_message``) is Band-side
delivery, never a provider failure -- even when the ``send_message`` call sits
inside a try/except that also handles real provider errors. ``deliver_reply``
wraps the cause in ``DeliveryFailedError`` so that shared except block can tell
the two apart and re-raise the original cause before its provider branch.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, NoReturn

from band.core.content import has_visible_content

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol

logger = logging.getLogger(__name__)


class DeliveryFailedError(Exception):
    """Keep Band audience resolution and posting failures separate from providers."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


def reraise_delivery_cause(e: DeliveryFailedError) -> NoReturn:
    """Log then re-raise a ``DeliveryFailedError``'s cause.

    Band-side reply delivery failed, never a provider failure -- re-raises
    the cause (not this wrapper) so mark_failed/retry bookkeeping keys off
    the real exception.
    """
    logger.exception("Reply delivery failed: %s", e.cause)
    raise e.cause from None


async def deliver_reply(
    tools: AgentToolsProtocol,
    content: str,
    mentions: list[str] | list[dict[str, str]] | None = None,
) -> Any:
    """Send the model's words as the turn's reply, raising
    ``DeliveryFailedError`` on failure instead of the raw exception, so the
    caller's except can distinguish a delivery failure from a provider
    failure."""
    try:
        return await tools.send_message(content, mentions=mentions)
    except Exception as exc:
        raise DeliveryFailedError(exc) from exc


async def deliver_notice(
    tools: AgentToolsProtocol,
    content: str,
    mentions: list[str] | list[dict[str, str]] | None = None,
) -> Any:
    """Post the adapter's own message via ``send_notice`` (never the turn's
    reply), raising ``DeliveryFailedError`` on failure like ``deliver_reply``."""
    try:
        return await tools.send_notice(content, mentions=mentions)
    except Exception as exc:
        raise DeliveryFailedError(exc) from exc


async def relay_reply(
    tools: AgentToolsProtocol,
    text: str | None,
    mentions: list[str] | list[dict[str, str]] | None = None,
) -> bool:
    """Relay the model's final text unless a tool call already replied or
    declined this turn; return whether it posted."""
    if text is None or tools.turn.replied or not has_visible_content(text):
        return False
    return await deliver_reply(tools, text, mentions) is not None


async def automatic_mentions(
    tools: AgentToolsProtocol, sender_ids: Sequence[str]
) -> list[dict[str, str]]:
    """Address original senders who remain in a fresh room snapshot."""
    try:
        audience = dict.fromkeys(sender_id for sender_id in sender_ids if sender_id)
        if not audience:
            raise ValueError("Automatic delivery requires an originating sender")
        participants = await tools.get_participants_snapshot()
        present_ids = {participant.id for participant in participants}
        return [{"id": sender_id} for sender_id in audience if sender_id in present_ids]
    except Exception as exc:
        raise DeliveryFailedError(exc) from exc


async def finish_automatic_reply(
    tools: AgentToolsProtocol,
    text: str | None,
    mentions: list[dict[str, str]],
) -> None:
    """Finish successful work after authority checks, preserving missing-reply judgment.

    Present recipients without visible text still require the normal turn verdict.
    """
    if tools.turn.replied:
        return
    if not mentions:
        if not tools.turn.complete:
            logger.debug("Settling completed provider work after its audience departed")
            tools.turn.settle()
        return
    if text is not None and has_visible_content(text):
        await deliver_reply(tools, text, mentions)
