"""Reply-presence waits on top of ``ReplyCapture``.

``ReplyCapture.wait_for_reply`` barriers on the platform's PROCESSED delivery
status before looking at reply frames. Not every Band client reports that
status (live-observed: some harnesses leave delivery at ``none`` even after
they visibly reply). These waits are reply-presence-based and harness-neutral
— still event-driven over the WS capture, no polling.

Both waits are best-effort: on the deadline they return whatever matched
rather than raising, so the caller's declarative assertion (``assert_present`` /
``assert_contains_any``) is what fails — naming the missing behavior — instead
of a bare ``TimeoutError`` from deep in the capture.
"""

from __future__ import annotations

from collections.abc import Sequence

from tests.e2e.baseline.toolkit.capture import ReplyCapture
from tests.e2e.baseline.toolkit.observations import Replies


def _authored(
    message, sender_id: str, containing: str | tuple[str, ...] | None
) -> bool:
    if message.sender_id != sender_id:
        return False
    if containing is None:
        return True
    tokens = (containing,) if isinstance(containing, str) else containing
    content = (message.content or "").lower()
    return any(token.lower() in content for token in tokens)


async def wait_for_reply_from(
    capture: ReplyCapture,
    sender_id: str,
    *,
    since: int = 0,
    containing: str | tuple[str, ...] | None = None,
    deadline_s: float | None = None,
) -> Replies:
    """Wait for a message authored by ``sender_id`` (optionally carrying
    ``containing`` — a tuple means any of its tokens settles the wait) at
    buffer index ``since`` or later; return that sender's replies from the
    window.

    ``since`` scopes the wait to the current turn of a multi-turn test — mark
    ``len(capture.messages)`` once the previous turn has settled — so an earlier
    turn's reply can never satisfy a later turn's wait. ``containing`` waits for
    the message that proves the behavior, not merely the first one: a sender's
    first message is not always the answer (live-observed, some agents post an
    interim notice before replying).

    ``containing`` gates only the WAIT; the returned window is the sender's full
    message set from ``since``, unfiltered — so assertions can inspect interim
    messages too (e.g. catch a leak in any of them). Contrast
    ``wait_for_replies_from``, which filters what it returns."""
    try:
        await capture.wait_until(
            lambda msgs: any(
                _authored(m, sender_id, containing) for m in msgs[since:]
            ),
            deadline_s=deadline_s,
        )
    except TimeoutError:
        pass  # best-effort: the caller's assertion reports the miss
    return Replies(m for m in capture.messages[since:] if m.sender_id == sender_id)


async def wait_for_replies_from(
    capture: ReplyCapture,
    sender_ids: Sequence[str],
    *,
    since: int = 0,
    containing: str | None = None,
    deadline_s: float | None = None,
) -> dict[str, Replies]:
    """Wait until every sender in ``sender_ids`` has authored a matching message
    at buffer index ``since`` or later (the fan-out settling condition); return
    the matching replies keyed by sender id. With ``containing``, a sender only
    counts once it produces a reply carrying that token, so an interim/non-
    answer message does not satisfy the wait and the empty entry names the
    silent sender.

    ``since`` scopes the wait and returned replies to the current round of a
    multi-round test, preventing an earlier fan-out reply from satisfying a
    later one.

    Unlike ``wait_for_reply_from``, here ``containing`` also FILTERS the returned
    per-sender ``Replies`` — each entry holds only the matching messages, so a
    fan-out assertion sees exactly the proving replies."""
    pending = list(sender_ids)
    try:
        await capture.wait_until(
            lambda msgs: all(
                any(_authored(m, sender, containing) for m in msgs[since:])
                for sender in pending
            ),
            deadline_s=deadline_s,
        )
    except TimeoutError:
        pass  # best-effort: per-sender assertions report which stayed silent
    return {
        sender: Replies(
            m
            for m in capture.messages[since:]
            if _authored(m, sender, containing)
        )
        for sender in pending
    }


def said_by(
    capture: ReplyCapture,
    sender_id: str,
    token: str,
    *,
    since: int = 0,
    excluding: str | None = None,
) -> Replies:
    """Messages authored by ``sender_id`` carrying ``token`` — built for absence
    assertions (``assert not said_by(...)``) as much as presence. ``excluding``
    drops messages that also carry that other token, so a reply that merely
    quotes earlier context alongside its own answer does not count.
    Pure observation over the already-captured buffer; nothing is awaited —
    the caller settles the window first (e.g. via a control turn)."""

    def matches(message) -> bool:
        if not _authored(message, sender_id, token):
            return False
        if excluding is None:
            return True
        return excluding.lower() not in (message.content or "").lower()

    return Replies(m for m in capture.messages[since:] if matches(m))
