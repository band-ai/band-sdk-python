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
    tools: "AgentToolsProtocol",
    content: str,
    mentions: list[str] | list[dict[str, str]] | None = None,
) -> Any:
    """Send a reply, raising ``DeliveryFailedError`` on failure instead of
    the raw exception, so the caller's except can distinguish a delivery
    failure from a provider failure."""
    try:
        return await tools.send_message(content, mentions=mentions)
    except Exception as exc:
        raise DeliveryFailedError(exc) from exc
