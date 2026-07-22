# /// script
# requires-python = ">=3.11"
# dependencies = ["band-sdk"]
#
# [tool.uv.sources]
# band-sdk = { git = "https://github.com/band-ai/band-sdk-python.git" }
# ///
"""Host-side conductor + circuit breaker for the three-agent Docker demo.

The conductor is the only actor that sits *outside* the sandboxes, so it is the
only thing that can guarantee the meeting ends. It creates the room, kicks off
the design discussion, then watches the room over the Human REST API — feeding
every text message to the pure ``CircuitBreaker`` (see breaker.py) and executing
whatever the breaker returns: a handoff nudge, an Architect add-fallback, an open
floor, or a stop. In the happy path it stays silent while the PM hands off and the
Architect decides.

The presenter (you) is a human participant in the same room. Interjections are
recorded but never counted toward the caps, so talking never trips the breaker.
Interactive runs (the default) hand the room to you once the Architect decides:
ask the agents anything, then end the meeting with an end phrase (``end meeting``
/ ``/end`` / ``wrap up``), by going idle, or with Ctrl-C. Headless runs
(``DEMO_INTERACTIVE=false``) skip the open floor and close on the verdict.

Run with:
    uv run examples/docker_demo/conductor.py
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from band_rest import AsyncRestClient, ChatMessageRequest, ParticipantRequest
from band_rest.human_api_chats.types.create_my_chat_room_request_chat import (
    CreateMyChatRoomRequestChat,
)
from band_rest.types import ChatMessage
from band_rest.types import ChatMessageRequestMentionsItem as Mention
from pydantic_settings import BaseSettings, SettingsConfigDict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from docker_demo.breaker import (  # noqa: E402  (path set up above)
    Action,
    BreakerConfig,
    CircuitBreaker,
    ObservedMessage,
    SenderClass,
)

from band.config import load_agent_config  # noqa: E402

logger = logging.getLogger(__name__)

# The Architect ends the meeting only with an explicit verdict; a "reviewing…"
# message must not. The architect persona is instructed to lead with this marker.
DECISION_MARKER = re.compile(r"\b(?:decision|verdict)\s*:", re.IGNORECASE)

# In the interactive open floor the presenter wraps up by posting any of these.
# Single source, surfaced verbatim in the open-floor invite so it's discoverable.
END_PHRASE = re.compile(
    r"(?:^/end\b|\bend meeting\b|\bwrap up\b|\badjourn\b)", re.IGNORECASE
)
END_PHRASE_HINT = "end meeting"

# BreakerConfig is the single source of the breaker cap defaults; the settings
# below default to it (env vars still override) so the two can't drift apart.
_BREAKER_DEFAULTS = BreakerConfig()


class ConductorSettings(BaseSettings):
    """Environment configuration for the conductor and the circuit breaker.

    Field name == env var name; caps mirror ``BreakerConfig`` so a presenter can
    retune the meeting live (e.g. ``DEMO_HARD_CAP=20``) without touching code.
    """

    model_config = SettingsConfigDict(
        extra="ignore", case_sensitive=False, env_ignore_empty=True
    )

    band_rest_url: str = "https://app.band.ai"
    band_api_key_user: str = ""  # conductor identity == the presenter's human seat

    demo_topic: str = "a way to run untrusted AI agents in isolated Docker sandboxes"
    demo_pm_key: str = "demo_pm"
    demo_dev_key: str = "demo_dev"
    demo_architect_key: str = "demo_architect"

    demo_poll_interval_s: float = 3.0
    demo_soft_cap: int = _BREAKER_DEFAULTS.soft_cap
    demo_handoff_deadline: int = _BREAKER_DEFAULTS.handoff_deadline
    demo_hard_cap: int = _BREAKER_DEFAULTS.hard_cap
    demo_wall_clock_s: float = _BREAKER_DEFAULTS.wall_clock_s
    demo_grace_s: float = _BREAKER_DEFAULTS.grace_s
    # Interactive: after the verdict, hand the room to the presenter instead of
    # closing on grace. launch.sh sets this False for headless/CI runs.
    demo_interactive: bool = True
    demo_open_floor_idle_s: float = _BREAKER_DEFAULTS.open_floor_idle_s
    # Web UI URL for the room, auto-opened by launch.sh. {chat_id} is filled in;
    # default derives from the REST host (override for a distinct web app host).
    demo_ui_url_template: str = ""

    def breaker_config(self) -> BreakerConfig:
        return BreakerConfig(
            soft_cap=self.demo_soft_cap,
            handoff_deadline=self.demo_handoff_deadline,
            hard_cap=self.demo_hard_cap,
            wall_clock_s=self.demo_wall_clock_s,
            grace_s=self.demo_grace_s,
            interactive=self.demo_interactive,
            open_floor_idle_s=self.demo_open_floor_idle_s,
        )


@dataclass
class Roster:
    """The room's known participants, used to classify who authored a message.

    Only the three agent ids are known up front; every other User (the presenter,
    the conductor's own posts) is a human. Anything unrecognized is UNKNOWN and,
    like humans, never counts toward the caps.
    """

    pm_id: str
    dev_id: str
    architect_id: str
    names: dict[str, str] = field(
        default_factory=dict
    )  # participant id -> display name

    def classify(self, message: ChatMessage) -> SenderClass:
        match message.sender_id:
            case self.pm_id:
                return SenderClass.PM
            case self.dev_id:
                return SenderClass.DEVELOPER
            case self.architect_id:
                return SenderClass.ARCHITECT
            case _:
                return (
                    SenderClass.HUMAN
                    if message.sender_type == "User"
                    else SenderClass.UNKNOWN
                )

    def mentions_architect(self, message: ChatMessage) -> bool:
        mentions = (message.metadata or {}).get("mentions") or []
        return any(
            str(m.get("id")) == self.architect_id
            for m in mentions
            if isinstance(m, dict)
        )

    def is_final_decision(self, message: ChatMessage) -> bool:
        """True only for an Architect message carrying an explicit verdict marker."""
        return self.classify(message) is SenderClass.ARCHITECT and bool(
            DECISION_MARKER.search(message.content or "")
        )


def to_observed(
    message: ChatMessage, roster: Roster, self_posted: set[str]
) -> ObservedMessage:
    """Project a platform ChatMessage into the transport-free shape the breaker consumes.

    ``self_posted`` holds ids of messages the conductor posted itself. Those arrive
    as the presenter's own identity (HUMAN), so they are excluded here — otherwise
    the invite/closer would masquerade as presenter activity and reset the idle timer.
    """
    ts = (
        message.inserted_at.timestamp()
        if message.inserted_at
        else dt.datetime.now(dt.timezone.utc).timestamp()
    )
    sender_class = roster.classify(message)
    is_presenter = sender_class is SenderClass.HUMAN and message.id not in self_posted
    return ObservedMessage(
        sender_class=sender_class,
        timestamp=ts,
        mentions_architect=roster.mentions_architect(message),
        is_final_decision=roster.is_final_decision(message),
        is_presenter=is_presenter,
        is_end_signal=is_presenter and bool(END_PHRASE.search(message.content or "")),
    )


class Conductor:
    """Drives the demo room and enforces the circuit breaker."""

    def __init__(
        self, client: AsyncRestClient, settings: ConductorSettings, roster: Roster
    ) -> None:
        self.client = client
        self.settings = settings
        self.roster = roster
        self.breaker = CircuitBreaker(settings.breaker_config(), start_time=self._now())
        self.chat_id: str = ""
        self._seen: set[str] = set()
        self._self_posted: set[str] = set()  # ids of messages we posted (not presenter)

    @staticmethod
    def _now() -> float:
        return dt.datetime.now(dt.timezone.utc).timestamp()

    def _room_title(self) -> str:
        """A readable, timestamped room name (topic + local date-time)."""
        topic = re.sub(r"^(?:a|an)\s+", "", self.settings.demo_topic).strip()
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        return f"Design Review — {topic} — {stamp}"

    def _mention(self, participant_id: str) -> Mention:
        return Mention(
            id=participant_id,
            name=self.roster.names.get(participant_id, participant_id),
        )

    async def _post(self, content: str, mention_ids: list[str] | None = None) -> None:
        """Send a facilitator message and remember its id.

        The conductor posts as the presenter's own identity, so recording the id
        lets ``to_observed`` tell our posts apart from the presenter's real input.
        """
        resp = await self.client.human_api_messages.send_my_chat_message(
            self.chat_id,
            message=ChatMessageRequest(
                content=content,
                mentions=[self._mention(m) for m in (mention_ids or [])],
            ),
        )
        if mid := getattr(resp.data, "id", None):
            self._self_posted.add(str(mid))

    async def setup(self) -> str:
        """Create the room, add the PM and Developer, and post the opening brief."""
        resp = await self.client.human_api_chats.create_my_chat_room(
            chat=CreateMyChatRoomRequestChat(title=self._room_title())
        )
        self.chat_id = resp.data.id
        logger.info("Created demo room: %s", self.chat_id)

        for agent_id in (self.roster.pm_id, self.roster.dev_id):
            await self.client.human_api_participants.add_my_chat_participant(
                self.chat_id,
                participant=ParticipantRequest(participant_id=agent_id, role="member"),
            )
        await self._refresh_names()

        # Brief goes to the PM alone (deterministic opening): she frames the problem
        # and pulls in the developer with a specific question, rather than both
        # agents opening at once. The developer is already a participant, so her
        # @mention reaches him.
        dev_name = self.roster.names[self.roster.dev_id]
        brief = (
            f"@{self.roster.names[self.roster.pm_id]} you're leading this design meeting: "
            f"design {self.settings.demo_topic} for a sprint MVP. Frame the problem and open the "
            f"discussion with the developer ({dev_name}), then bring in the architect for the "
            f"final decision once you've aligned."
        )
        await self._post(brief, [self.roster.pm_id])
        logger.info("Kicked off design of %s", self.settings.demo_topic)
        self._emit_room_url()
        return self.chat_id

    def _emit_room_url(self) -> None:
        """Write the room's web URL so launch.sh can open the Band UI to it."""
        template = (
            self.settings.demo_ui_url_template
            or f"{self.settings.band_rest_url}/chat/{{chat_id}}"
        )
        url = template.format(chat_id=self.chat_id)
        url_file = Path(__file__).parent / ".demo" / "room.url"
        url_file.parent.mkdir(exist_ok=True)
        url_file.write_text(url, encoding="utf-8")
        logger.info("Room UI URL: %s", url)

    async def _refresh_names(self) -> None:
        parts = await self.client.human_api_participants.list_my_chat_participants(
            self.chat_id
        )
        self.roster.names.update({str(p.id): (p.name or str(p.id)) for p in parts.data})

    async def _new_messages(self) -> list[ChatMessage]:
        """Return unseen text messages in chronological order."""
        resp = await self.client.human_api_messages.list_my_chat_messages(
            self.chat_id, message_type="text", limit=100
        )
        fresh = [m for m in resp.data if m.id not in self._seen]
        self._seen.update(m.id for m in fresh)
        # inserted_at is optional and platform stamps are tz-aware; an unstamped
        # message must sort as oldest with a tz-aware sentinel — a naive one would
        # raise TypeError against the aware stamps and crash the loop.
        oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        return sorted(fresh, key=lambda m: m.inserted_at or oldest)

    async def _architect_in_room(self) -> bool:
        parts = await self.client.human_api_participants.list_my_chat_participants(
            self.chat_id
        )
        return any(str(p.id) == self.roster.architect_id for p in parts.data)

    async def _nudge_handoff(self) -> None:
        pm = self.roster.names[self.roster.pm_id]
        await self._post(
            f"[facilitator] @{pm} you've aligned enough — please bring in the architect for a decision.",
            [self.roster.pm_id],
        )
        logger.info("Nudged PM to hand off")

    async def _add_architect(self) -> None:
        """Idempotent add-fallback: add the Architect only if the PM never did."""
        if await self._architect_in_room():
            self.breaker.note_architect_present()
            return
        await self.client.human_api_participants.add_my_chat_participant(
            self.chat_id,
            participant=ParticipantRequest(
                participant_id=self.roster.architect_id, role="member"
            ),
        )
        await self._refresh_names()
        arch = self.roster.names.get(self.roster.architect_id, "architect")
        await self._post(
            f"[facilitator] @{arch} please review the design above and give a decision.",
            [self.roster.architect_id],
        )
        self.breaker.note_architect_present()
        logger.info("Add-fallback: added architect to room")

    async def _open_floor(self) -> None:
        """Verdict reached: invite the presenter to drive, naming the end phrase."""
        agents = [self.roster.pm_id, self.roster.dev_id, self.roster.architect_id]
        handles = " ".join(f"@{self.roster.names.get(a, a)}" for a in agents)
        await self._post(
            f"[facilitator] ✅ Decision reached — the floor is yours. {handles} are "
            f'still here; ask them anything. Post "{END_PHRASE_HINT}" when you\'re '
            f"done and I'll wrap up and tear down.",
            agents,
        )
        logger.info("Opened the floor to the presenter")

    async def _close(self, reason: str) -> None:
        await self._post(
            f"[facilitator] Wrapping up the design meeting ({reason}). Thanks all.",
            [self.roster.pm_id],
        )
        logger.info("Posted closer (%s)", reason)

    async def _apply(self, actions: list[Action]) -> str | None:
        """Execute breaker actions; return a stop reason once a terminal one fires."""
        for action in actions:
            match action:
                case Action.NUDGE_HANDOFF:
                    await self._nudge_handoff()
                case Action.ADD_ARCHITECT:
                    await self._add_architect()
                case Action.OPEN_FLOOR:
                    await self._open_floor()
                case Action.TERMINATE_OK:
                    await self._close(self.breaker.stop_reason or "design decided")
                    return "terminate_ok"
                case Action.HARD_KILL:
                    await self._close("time/turn limit reached")
                    return "hard_kill"
        return None

    async def run(self) -> str:
        """Poll the room, drive the breaker, and act until a terminal decision.

        Returns the stop reason ("terminate_ok" | "hard_kill"). The caller tears
        down the sandboxes, which is what actually silences the agents.
        """
        await self.setup()
        reason: str | None = None
        # Guard the loop with the breaker: on exit it marks itself terminal (a
        # defensive backstop). Stopping the agents is teardown's job, not this flag's.
        with self.breaker:
            while reason is None:
                await asyncio.sleep(self.settings.demo_poll_interval_s)
                for message in await self._new_messages():
                    self.breaker.record(
                        to_observed(message, self.roster, self._self_posted)
                    )
                reason = await self._apply(self.breaker.poll(self._now()))
        logger.info("Conductor finished: %s", reason)
        return reason


def build_roster(settings: ConductorSettings) -> Roster:
    # Anchor the config to the demo directory so the conductor works from any CWD.
    config_path = Path(__file__).parent / "agent_config.yaml"
    pm_id, _ = load_agent_config(settings.demo_pm_key, config_path=config_path)
    dev_id, _ = load_agent_config(settings.demo_dev_key, config_path=config_path)
    architect_id, _ = load_agent_config(
        settings.demo_architect_key, config_path=config_path
    )
    return Roster(pm_id=pm_id, dev_id=dev_id, architect_id=architect_id)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [conductor] %(message)s"
    )
    settings = ConductorSettings()
    if not settings.band_api_key_user:
        raise ValueError(
            "BAND_API_KEY_USER is required (the conductor/presenter identity)"
        )

    roster = build_roster(settings)
    client = AsyncRestClient(
        api_key=settings.band_api_key_user, base_url=settings.band_rest_url
    )
    conductor = Conductor(client, settings, roster)
    reason = await conductor.run()
    logger.info("Demo room %s ended: %s", conductor.chat_id, reason)


if __name__ == "__main__":
    asyncio.run(main())
