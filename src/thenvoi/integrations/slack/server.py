"""HTTP server for the Slack bridge adapter.

Builds a Starlette router serving Slack Events API webhooks for each
configured Slack app. Each app is served at ``/{slug}/events``.

Responsibilities of this layer:

- HMAC signature verification
- ``url_verification`` challenge handling
- Event-ID dedup for Slack retries
- Dispatching verified events to a per-app callback
"""

from __future__ import annotations

import collections
import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route, Router

from thenvoi.integrations.slack.signature import verify_signature

if TYPE_CHECKING:
    from thenvoi.integrations.slack.types import SlackApp

logger = logging.getLogger(__name__)

# Type alias for the per-app event dispatcher.
EventDispatcher = Callable[["SlackApp", dict], Awaitable[None]]

# Default bounded cache size for Slack event-ID dedup. Slack retries
# within ~10 minutes of an unacked event; sizing for several minutes of
# events at moderate throughput gives comfortable headroom.
DEFAULT_SEEN_EVENTS_CACHE_SIZE = 10_000


class _SeenEvents:
    """LRU-bounded set of recently-seen Slack ``event_id`` values.

    Used to dedup Slack retries: any event with an ``event_id`` we've
    already processed is dropped and acked 200. The cache is bounded
    so it can't grow unbounded over the process lifetime; the eviction
    policy is least-recently-seen.

    Thread-safety: not used from multiple threads. The Slack handlers
    run on a single asyncio event loop, so we don't need locking.
    """

    def __init__(self, max_size: int = DEFAULT_SEEN_EVENTS_CACHE_SIZE) -> None:
        self._seen: collections.OrderedDict[str, None] = collections.OrderedDict()
        self._max_size = max_size

    def is_dupe(self, event_id: str) -> bool:
        """Return True if ``event_id`` was seen before. Records it either way."""
        if event_id in self._seen:
            self._seen.move_to_end(event_id)
            return True
        self._seen[event_id] = None
        if len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False

    def __len__(self) -> int:
        return len(self._seen)


def build_router(
    apps: list[SlackApp],
    *,
    dispatcher: EventDispatcher | None = None,
    seen_events: _SeenEvents | None = None,
) -> Router:
    """Construct a Starlette router serving all configured Slack apps.

    Args:
        apps: Configured Slack apps; one route per app at
            ``/{slug}/events``. Must be non-empty and have unique slugs.
        dispatcher: Optional async callback invoked with
            ``(app, payload)`` for non-``url_verification`` events.
        seen_events: Optional shared dedup cache for testing. Defaults
            to a fresh ``_SeenEvents`` shared across all apps in the
            router.

    Returns:
        A Starlette ``Router`` ready to be mounted at any prefix.

    Raises:
        ValueError: If ``apps`` is empty or contains duplicate slugs.
    """
    if not apps:
        raise ValueError("build_router requires at least one SlackApp")

    seen_events = seen_events or _SeenEvents()

    seen_slugs: set[str] = set()
    routes: list[Route] = []
    for app in apps:
        if app.slug in seen_slugs:
            raise ValueError(f"Duplicate SlackApp slug: {app.slug!r}")
        seen_slugs.add(app.slug)
        routes.append(
            Route(
                f"/{app.slug}/events",
                _build_handler(app, dispatcher, seen_events),
                methods=["POST"],
            )
        )
    return Router(routes=routes)


def _build_handler(
    app: SlackApp,
    dispatcher: EventDispatcher | None,
    seen_events: _SeenEvents,
) -> Callable[[Request], Awaitable[Response]]:
    """Build a request handler bound to one ``SlackApp``."""

    async def handle(request: Request) -> Response:
        body = await request.body()
        timestamp = request.headers.get("x-slack-request-timestamp", "")
        signature = request.headers.get("x-slack-signature", "")

        if not verify_signature(
            signing_secret=app.signing_secret,
            body=body,
            timestamp=timestamp,
            signature=signature,
        ):
            logger.warning(
                "Rejected Slack request for app %s: signature verification failed",
                app.slug,
            )
            return PlainTextResponse("invalid signature", status_code=401)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.warning(
                "Rejected Slack request for app %s: body is not valid JSON", app.slug
            )
            return PlainTextResponse("invalid JSON", status_code=400)

        if payload.get("type") == "url_verification":
            challenge = payload.get("challenge", "")
            return PlainTextResponse(challenge, status_code=200)

        # Retry idempotency: if Slack has already delivered this event
        # to us (and our 200 was slow / lost), drop the duplicate. Ack
        # 200 so Slack stops retrying.
        event_id = payload.get("event_id")
        if event_id and seen_events.is_dupe(event_id):
            retry_num = request.headers.get("x-slack-retry-num", "?")
            retry_reason = request.headers.get("x-slack-retry-reason", "")
            logger.info(
                "Dropping duplicate Slack event %s for app %s (retry=%s reason=%s)",
                event_id,
                app.slug,
                retry_num,
                retry_reason,
            )
            return Response(status_code=200)

        if dispatcher is not None:
            try:
                await dispatcher(app, payload)
            except Exception:
                logger.exception(
                    "Dispatcher raised while handling Slack event for app %s",
                    app.slug,
                )

        return Response(status_code=200)

    return handle
