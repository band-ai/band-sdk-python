"""Types for the Slack bridge adapter."""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """Session state for the Slack adapter.

    Repopulated by ``SlackHistoryConverter`` when the agent rejoins a
    room. The mapping is what lets the adapter route inner-brain replies
    back to the right Slack thread.

    Attributes:
        thread_to_room: ``"{channel_id}:{thread_ts}"`` keys → Thenvoi
            room IDs. Keys are flattened to strings so the state round-
            trips through history-event metadata.
    """

    thread_to_room: dict[str, str] = field(default_factory=dict)


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
