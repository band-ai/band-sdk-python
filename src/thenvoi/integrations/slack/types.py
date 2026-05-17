"""Types for the Slack bridge adapter."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SlackApp:
    """Configuration for one Slack app served by the adapter.

    One ``SlackApp`` = one Slack bot user attached to one Thenvoi agent.
    Multiple ``SlackApp`` entries can share a single adapter (e.g. for
    multi-tenant setups); each gets its own HTTP route at
    ``/{slug}/events``.

    Attributes:
        slug: URL-safe identifier; used as the HTTP route segment.
        signing_secret: The Slack app's signing secret (HMAC verification).
        bot_token: The Slack app's bot token (``xoxb-...``) for outbound
            ``chat.postMessage`` etc.
    """

    slug: str
    signing_secret: str
    bot_token: str


@dataclass
class SlackSessionState:
    """Per-room session state recovered from platform history.

    Platform history is fetched and converted per-room, so the converter
    only ever sees the events for one room at a time. The room_id itself
    comes from ``inp.room_id``; the converter just needs to surface the
    Slack thread identity stored in the room's bootstrap context event.

    Attributes:
        binding: Slack thread bound to this room, or ``None`` if the
            history contains no ``slack_app_slug`` task event (e.g. the
            room wasn't created by this bridge, or the bootstrap event
            was scrubbed).
    """

    binding: SlackRoomBinding | None = None


@dataclass(frozen=True)
class SlackRoomBinding:
    """Records the Slack thread that a Thenvoi room mirrors.

    Used to route brain replies back: when ``tools.send_message`` fires
    for a bound room, the adapter looks up the binding to know which
    Slack channel/thread to ``chat.postMessage`` into.

    Attributes:
        app_slug: Which ``SlackApp`` owns this conversation (selects the
            bot token used for the outbound Slack call).
        channel: Slack channel/DM ID the original message came from.
        thread_ts: Slack thread root timestamp; replies post under this
            ``thread_ts`` so the conversation stays threaded.
    """

    app_slug: str
    channel: str
    thread_ts: str
