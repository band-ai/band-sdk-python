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

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route, Router

# Re-exported for backward compatibility — the dedup cache now lives in a
# starlette-free module so Socket Mode can share it.
from band.integrations.slack.dedup import (
    DEFAULT_SEEN_EVENTS_CACHE_SIZE,
    _SeenEvents,
)
from band.integrations.slack.signature import verify_signature

if TYPE_CHECKING:
    from band.integrations.slack.types import SlackApp

logger = logging.getLogger(__name__)

# Type alias for the per-app event dispatcher.
EventDispatcher = Callable[["SlackApp", dict], Awaitable[None]]

__all__ = [
    "DEFAULT_SEEN_EVENTS_CACHE_SIZE",
    "_SeenEvents",
    "build_router",
]


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
