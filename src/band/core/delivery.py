"""Delivery-vs-provider-failure misclassification guard.

An adapter's reply/bookkeeping post to the room (``send_message``) is Band-side
delivery, never a provider failure -- even when the ``send_message`` call sits
inside a try/except that also handles real provider errors. ``deliver_reply``
wraps the cause in ``DeliveryFailedError`` so that shared except block can tell
the two apart and re-raise the original cause before its provider branch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, NoReturn

from band.core.content import has_visible_content
from band.core.protocols import send_event_safe
from band.core.types import AssistantTextMode, Emit, MessageType

if TYPE_CHECKING:
    from band.core.protocols import AgentToolsProtocol

logger = logging.getLogger(__name__)


class DeliveryFailedError(Exception):
    """Wraps a ``send_message`` failure so it is never mistaken for a
    provider failure by a shared except block."""

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


async def handle_assistant_text(
    tools: AgentToolsProtocol,
    text: str | None,
    mentions: list[str] | list[dict[str, str]] | None,
    *,
    mode: AssistantTextMode,
    emit: frozenset[Emit],
) -> bool:
    """Deliver the model's final text as ``mode`` says; return whether it
    posted a reply.

    Text a Band tool already answered or declined is never repeated. As a
    thought, the text settles the turn: the operator chose to read such text
    as the agent's own narration, not a reply the runtime should report as
    missing. After a reply attempt that did not land it does not: that text
    may describe a reply the room never received, so the turn still owes one.
    """
    if mode is AssistantTextMode.REPLY:
        return await relay_reply(tools, text, mentions)
    if text is None or tools.turn.replied or not has_visible_content(text):
        return False
    if Emit.THOUGHTS in emit:
        await send_event_safe(
            tools,
            content=text,
            message_type=MessageType.THOUGHT,
            log_label="assistant text thought",
        )
    if not tools.turn.reply_attempted:
        tools.turn.settle()
    return False
