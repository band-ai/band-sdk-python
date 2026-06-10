"""Socket Mode transport for the Slack bridge.

Socket Mode is Slack's websocket-based alternative to webhooks: instead
of Slack POSTing events to a public URL, the bridge opens a websocket
per Slack app and Slack pushes events through it. There's no public URL
to manage, no signing-secret HMAC to verify (Slack authenticates the
websocket via an app-level ``xapp-...`` token), and no HTTP server to
run — which makes this the path of least resistance for local dev,
firewalled deployments, and dev-without-ngrok workflows.

Downstream of the websocket the pipeline is identical to the HTTP
transport: the same ``SlackAdapter._dispatch_event`` ack-then-async
handler that powers ``transport="http"`` is invoked with the same
``(app, payload)`` shape. Only the ingress differs.

Slack still enforces the 3-second ack window over Socket Mode, so the
listener acks the envelope first and lets ``_dispatch_event`` queue the
real work into a background asyncio task.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from band.integrations.slack.dedup import _SeenEvents

if TYPE_CHECKING:
    from slack_sdk.socket_mode.aiohttp import SocketModeClient
    from slack_sdk.web.async_client import AsyncWebClient

    from band.integrations.slack.types import SlackApp

logger = logging.getLogger(__name__)


# Signature mirrors ``SlackAdapter._dispatch_event``: takes the app config
# and the raw Slack payload, schedules background work, returns fast.
SocketDispatcher = Callable[["SlackApp", dict[str, Any]], Awaitable[None]]


class SlackSocketListener:
    """One Slack app's Socket Mode websocket lifecycle.

    Holds a started ``SocketModeClient`` and the ``SlackApp`` it belongs
    to. ``stop()`` cleanly closes the websocket; failure is logged and
    swallowed so a misbehaving app doesn't block adapter shutdown.
    """

    def __init__(self, *, app: SlackApp, client: SocketModeClient) -> None:
        self.app = app
        self._client = client

    @property
    def client(self) -> SocketModeClient:
        """The underlying ``SocketModeClient`` (exposed for tests)."""
        return self._client

    async def stop(self) -> None:
        """Close the websocket. Idempotent enough for shutdown paths."""
        await self._client.disconnect()


async def start_socket_listeners(
    *,
    apps: list[SlackApp],
    web_client_factory: Callable[[SlackApp], AsyncWebClient],
    dispatcher: SocketDispatcher,
    client_factory: (
        Callable[[SlackApp, AsyncWebClient], SocketModeClient] | None
    ) = None,
    seen_events: _SeenEvents | None = None,
) -> list[SlackSocketListener]:
    """Open one Socket Mode websocket per ``SlackApp`` and start listening.

    Args:
        apps: Configured Slack apps. Each must carry an ``app_token``
            (``xapp-...``); validation is the adapter's responsibility.
        web_client_factory: Returns the ``AsyncWebClient`` to use for
            outbound API calls (e.g. ack sends). Shared with the rest of
            ``SlackAdapter`` so token + base config stay in one place.
        dispatcher: Async callable that receives an ``(app, payload)``
            tuple per Slack Events API envelope, mirroring
            ``SlackAdapter._dispatch_event``. The listener acks the
            envelope first, so the dispatcher is free to return after
            scheduling background work.
        client_factory: Test seam. Defaults to constructing
            ``slack_sdk.socket_mode.aiohttp.SocketModeClient`` with the
            app's ``app_token`` and the provided web client.
        seen_events: Optional shared ``event_id`` dedup cache. Defaults to
            a fresh one shared across all apps' listeners. Socket Mode can
            redeliver the same event across reconnects, so we drop dupes
            before dispatching (parallel to the HTTP route's dedup).

    Returns:
        Connected listeners, in input order, so ``SlackAdapter`` can hold
        the handles for ``close()`` teardown.

    Raises:
        ImportError: If ``slack-sdk`` or ``aiohttp`` is unavailable; only
            triggered when ``transport="socket"``.
    """
    factory = client_factory or _default_client_factory
    seen_events = seen_events or _SeenEvents()
    listeners: list[SlackSocketListener] = []
    for app in apps:
        web_client = web_client_factory(app)
        client = factory(app, web_client)
        client.socket_mode_request_listeners.append(
            _make_request_handler(
                app=app, dispatcher=dispatcher, seen_events=seen_events
            )
        )
        await client.connect()
        listeners.append(SlackSocketListener(app=app, client=client))
        logger.info(
            "Slack Socket Mode connected (app=%s)",
            app.slug,
        )
    return listeners


def _default_client_factory(
    app: SlackApp, web_client: AsyncWebClient
) -> SocketModeClient:
    try:
        from slack_sdk.socket_mode.aiohttp import SocketModeClient
    except ImportError as exc:  # pragma: no cover — import-time guard
        raise ImportError(
            "Socket Mode requires aiohttp. Install with "
            "`uv add aiohttp` (or `pip install aiohttp`); slack-sdk's "
            "aiohttp Socket Mode client depends on it."
        ) from exc
    return SocketModeClient(app_token=app.app_token, web_client=web_client)


def _make_request_handler(
    *,
    app: SlackApp,
    dispatcher: SocketDispatcher,
    seen_events: _SeenEvents,
) -> Callable[[SocketModeClient, Any], Awaitable[None]]:
    """Build a per-app Socket Mode request listener.

    Acks every envelope immediately (Slack will retry otherwise), then
    routes Events API payloads through ``dispatcher``. Slash commands
    and interactive components are out of v1 scope; we still ack them to
    avoid retries. Redelivered events (same ``event_id``) are dropped via
    ``seen_events`` so a reconnect can't double-invoke the brain.
    """
    from slack_sdk.socket_mode.response import SocketModeResponse

    async def handle(client: SocketModeClient, req: Any) -> None:
        envelope_id = getattr(req, "envelope_id", None)
        if envelope_id is not None:
            try:
                await client.send_socket_mode_response(
                    SocketModeResponse(envelope_id=envelope_id)
                )
            except Exception:
                logger.exception(
                    "Failed to ack Slack Socket Mode envelope (app=%s envelope_id=%s)",
                    app.slug,
                    envelope_id,
                )

        if getattr(req, "type", None) != "events_api":
            logger.debug(
                "Ignoring non-events_api Socket Mode payload (app=%s type=%s)",
                app.slug,
                getattr(req, "type", None),
            )
            return

        payload = getattr(req, "payload", None) or {}
        if not isinstance(payload, dict):
            logger.warning(
                "Slack Socket Mode payload not a dict (app=%s type=%s)",
                app.slug,
                type(payload).__name__,
            )
            return

        # Drop redeliveries before dispatching — the envelope is already
        # acked above, so we just skip the duplicate work.
        event_id = payload.get("event_id")
        if event_id and seen_events.is_dupe(event_id):
            logger.info(
                "Dropping duplicate Slack Socket Mode event %s (app=%s)",
                event_id,
                app.slug,
            )
            return

        try:
            await dispatcher(app, payload)
        except Exception:
            logger.exception(
                "Slack Socket Mode dispatcher raised (app=%s)",
                app.slug,
            )

    return handle
