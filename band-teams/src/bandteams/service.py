"""Run isolated enterprise sessions and supplied-room qualification routes."""

from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from microsoft_teams.api import MessageActivity, MessageActivityInput
from microsoft_teams.apps import ActivityContext, App
from phoenix_channels_python_client import PHXChannelsClient
from phoenix_channels_python_client.phx_messages import ChannelMessage

from bandteams.auth import OAuth, Tokens
from bandteams.evidence import Claim, Journal
from bandteams.proposals import Dispatcher, Proposals, Refused
from bandteams.settings import (
    HOSTED_REST,
    HOSTED_WS,
    REQUEST_SECONDS,
    BandSettings,
    Binding,
    Manifest,
    load_manifest,
)
from bandteams.teams import Teams, envelope
from bandteams.transport import Band

logger = logging.getLogger(__name__)


@dataclass
class Session:
    person: Binding
    oauth: OAuth
    tokens: Tokens
    band: Band
    socket: PHXChannelsClient | None = None
    ready: bool = False
    roots: dict[str, str] = field(default_factory=dict)
    recipients: dict[str, str] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Service:
    """Own session tasks, generation fences and local durable correlation."""

    def __init__(
        self,
        app: App,
        manifest: Manifest,
        settings: BandSettings,
        journal: Journal,
        manifest_file: Path,
    ) -> None:
        self.manifest_file = manifest_file
        self.app = app
        self.manifest = manifest
        self.settings = settings
        self.journal = journal
        self.proposals = Proposals(journal)
        self.dispatcher = Dispatcher(
            self.proposals, self.check, self.send, self.receipt
        )
        self.teams = Teams(
            app, manifest, self.proposals, self.dispatcher, authorize=self.authorize
        )
        self.sessions: dict[str, Session] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.http = httpx.AsyncClient(timeout=REQUEST_SECONDS, follow_redirects=False)
        app.on_message(self.message)
        app.on_card_action(self.teams.action)

    def authorize(self, tenant: str, actor: str) -> Binding:
        if tenant != self.manifest.people[0].tenant_id:
            raise Refused("Foreign tenant")
        return load_manifest(self.manifest_file).person(tenant, actor)

    async def roster(self, person: Binding) -> None:
        if self.authorize(person.tenant_id, person.actor_id) != person:
            raise Refused("Employee configuration changed; sign in again")
        members: list[str | None] = []
        cursor = None
        seen: set[str] = set()
        while True:
            page = await self.app.api.conversations.get_paged_members(
                person.team_id, continuation_token=cursor
            )
            members.extend(member.aad_object_id for member in page.members)
            cursor = page.continuation_token
            if not cursor:
                break
            if cursor in seen:
                raise Refused("Incomplete Teams roster")
            seen.add(cursor)
        if members != [person.actor_id]:
            raise Refused("Destination must contain exactly its linked employee")

    def current(self, alias: str) -> Session:
        session = self.sessions.get(alias)
        if session is None:
            raise Refused("Sign in first")
        return session

    async def check(self, claim: Claim) -> None:
        person = self.authorize(claim.tenant, claim.actor)
        session = self.current(person.alias)
        if not session.ready or session.person != person:
            raise Refused("Confirm the current destination first")
        async with asyncio.timeout(REQUEST_SECONDS):
            await self.roster(person)
            await session.band.identity(person)
            await session.band.participants(person, claim.room, claim.recipient)
        if self.sessions.get(person.alias) is not session or not session.ready:
            raise Refused("Link retired during access checks")

    async def send(self, claim: Claim, body: str) -> str:
        session = self.current(claim.alias)
        async with asyncio.timeout(REQUEST_SECONDS):
            return await session.band.send(claim.room, claim.recipient, body)

    async def receipt(self, claim: Claim) -> None:
        await self.check(claim)
        async with asyncio.timeout(REQUEST_SECONDS):
            sent = await self.app.send(
                claim.conversation,
                claim.delivery.value + (f" — {claim.band_id}" if claim.band_id else ""),
            )
        self.journal.resource(claim.alias, claim.conversation, sent.id)

    async def socket(self, session: Session) -> None:
        if session.socket is not None:
            await session.socket.__aexit__(None, None, None)
            session.socket = None
        socket = PHXChannelsClient(
            HOSTED_WS,
            "",
            auto_reconnect=False,
            additional_headers={
                "x-auth-token": session.tokens.access_token.get_secret_value()
            },
        )
        session.socket = socket
        await socket.__aenter__()
        await socket.subscribe_to_topic(f"user_rooms:{session.person.band_user_id}")
        for room in session.person.rooms:
            await socket.subscribe_to_topic(
                f"chat_room:{room.id}", lambda message: self.forward(session, message)
            )
            await socket.subscribe_to_topic(f"room_participants:{room.id}")

    async def forward(self, session: Session, message: ChannelMessage) -> None:
        if str(message.event) != "message_created" or not session.ready:
            return
        payload = message.payload
        user = session.person.band_user_id
        mentions = (payload.get("metadata") or {}).get("mentions", [])
        if payload.get("sender_id") == user or not any(
            m.get("id") == user and m.get("kind", "mention") == "mention"
            for m in mentions
        ):
            return
        room = message.subtopic
        if not room or room not in session.roots:
            return
        key = str(payload.get("id") or "")
        if not key:
            raise Refused("Band event has no identifier")
        async with session.lock:
            if key in session.seen:
                return
            await self.roster(session.person)
            await session.band.participants(
                session.person,
                room,
                session.recipients.get(
                    room,
                    next(
                        fixture.recipients[0]
                        for fixture in session.person.rooms
                        if fixture.id == room
                    ),
                ),
            )
            if (
                self.sessions.get(session.person.alias) is not session
                or not session.ready
            ):
                return
            session.seen.add(key)
            self.journal.append(
                "relay",
                {
                    "alias": session.person.alias,
                    "message": key,
                    "room": room,
                    "outcome": "attempted",
                },
            )
            body = str(payload.get("content") or "")
            if len(body) > 4000:
                body = body[:4000] + "… [preview shortened; open in Band]"
            text = f"<at>{html.escape(session.person.alias)}</at> {html.escape(str(payload.get('sender_name') or 'Band participant'))}: {html.escape(body)}"
            activity = MessageActivityInput(
                text=text,
                text_format="xml",
                entities=[
                    {
                        "type": "mention",
                        "text": f"<at>{html.escape(session.person.alias)}</at>",
                        "mentioned": {
                            "id": session.person.actor_id,
                            "name": session.person.alias,
                        },
                    }
                ],
            )
            sent = await self.app.reply(
                session.person.channel_id, session.roots[room], activity
            )
            self.journal.resource(
                session.person.alias, session.person.channel_id, sent.id
            )

    async def signin(self, person: Binding, conversation: str) -> None:
        oauth = OAuth(self.http, self.settings.BAND_OAUTH_CLIENT_ID)
        code = await oauth.start()
        await self.app.send(
            conversation,
            f"Open {code.verification_uri} and enter {code.user_code.get_secret_value()}. Never share this code.",
        )
        tokens = await oauth.finish(code)
        band = Band(tokens)
        session = Session(person, oauth, tokens, band)
        try:
            await band.identity(person)
        except BaseException:
            await band.close()
            raise
        self.sessions[person.alias] = session
        await self.app.send(
            conversation,
            "Signed in. Use confirm destination to check and enable your supplied rooms.",
        )

    async def destination(self, session: Session) -> None:
        async with session.lock:
            await self.roster(session.person)
            for room in session.person.rooms:
                await session.band.participants(
                    session.person, room.id, room.recipients[0]
                )
            for room in session.person.rooms:
                old = [
                    row["value"]
                    for row in self.journal.records
                    if row["kind"] == "root"
                    and row["value"]["alias"] == session.person.alias
                    and row["value"]["room"] == room.id
                ]
                if old:
                    if old[-1].get("binding") != [
                        session.person.band_user_id,
                        session.person.team_id,
                        session.person.channel_id,
                    ]:
                        raise Refused(
                            "Stored route belongs to a different identity or destination"
                        )
                    root = old[-1].get("activity")
                    if not root:
                        raise Refused(
                            "Root creation outcome unknown; operator reconciliation required"
                        )
                else:
                    self.journal.append(
                        "root",
                        {
                            "alias": session.person.alias,
                            "room": room.id,
                            "binding": [
                                session.person.band_user_id,
                                session.person.team_id,
                                session.person.channel_id,
                            ],
                            "activity": "",
                        },
                    )
                    sent = await self.app.send(
                        session.person.channel_id,
                        f"Band room {room.id} — addressed messages only. Open in Band: {HOSTED_REST}/chats/{room.id}",
                    )
                    root = sent.id
                    if not root.strip():
                        raise Refused("Root creation outcome unknown")
                    self.journal.resource(
                        session.person.alias, session.person.channel_id, root
                    )
                    self.journal.append(
                        "root",
                        {
                            "alias": session.person.alias,
                            "room": room.id,
                            "binding": [
                                session.person.band_user_id,
                                session.person.team_id,
                                session.person.channel_id,
                            ],
                            "activity": root,
                        },
                    )
                session.roots[room.id] = root
                if len(room.recipients) == 1:
                    session.recipients[room.id] = room.recipients[0]
            await self.socket(session)
            self.proposals.activate(session.person)
            session.ready = True

    async def refresh(self, session: Session) -> None:
        async with session.lock:
            session.ready = False
            self.proposals.retire(session.person.alias)
            tokens = await session.oauth.refresh(session.tokens)
            band = Band(tokens)
            try:
                await band.identity(session.person)
            except BaseException:
                await band.close()
                raise
            await session.band.close()
            session.band, session.tokens = band, tokens
            await self.socket(session)
            self.proposals.activate(session.person)
            session.ready = bool(session.roots)

    async def signout(self, alias: str) -> None:
        self.proposals.retire(alias)
        session = self.sessions.pop(alias, None)
        if session is not None:
            session.ready = False
        task = self.tasks.pop(alias, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.dispatcher.stop(alias)
        if session is not None:
            if session.socket is not None:
                await session.socket.__aexit__(None, None, None)
            await session.band.close()

    async def command(self, person: Binding, conversation: str, text: str) -> None:
        try:
            match text:
                case "sign in" | "new code":
                    await self.signin(person, conversation)
                case "confirm destination":
                    await self.destination(self.current(person.alias))
                    await self.app.send(
                        conversation,
                        "Ready. Reply in a supplied room thread; review before Send to Band.",
                    )
                case "refresh":
                    await self.refresh(self.current(person.alias))
                    await self.app.send(
                        conversation,
                        "Refreshed; WebSocket recreated with fresh headers. Old reviews are retired.",
                    )
                case _:
                    raise Refused("Unsupported command")
        except Exception:  # noqa: BLE001 - isolate one user without exposing provider payloads
            session = self.sessions.get(person.alias)
            if session is not None:
                session.ready = False
            logger.warning("Qualification command failed for %s", person.alias)
            await self.app.send(
                conversation,
                "Operation failed; status is not Ready. Check operator prerequisites and sign in again.",
            )
        finally:
            self.tasks.pop(person.alias, None)

    async def message(self, ctx: ActivityContext[MessageActivity]) -> None:
        activity = ctx.activity.model_dump(by_alias=True, exclude_none=True)
        origin = envelope(activity)
        try:
            person = self.authorize(origin.tenant, origin.actor)
            if activity.get("conversation", {}).get("conversationType") == "personal":
                text = (activity.get("text") or "").strip().lower()
                match text:
                    case "sign out":
                        await self.signout(person.alias)
                        await ctx.send("Signed out. Existing Teams copies remain.")
                    case "status":
                        session = self.sessions.get(person.alias)
                        await ctx.send(
                            "Ready"
                            if session and session.ready
                            else "Not Ready; sign in and confirm destination"
                        )
                    case "sign in" | "new code" | "confirm destination" | "refresh":
                        if text in ("sign in", "new code"):
                            await self.signout(person.alias)
                        if person.alias in self.tasks:
                            raise Refused("Another setup operation is running")
                        self.tasks[person.alias] = asyncio.create_task(
                            self.command(person, origin.conversation, text)
                        )
                        await ctx.send("Processing; use status to check progress")
                    case _:
                        parts = (activity.get("text") or "").split()
                        if len(parts) == 2 and parts[0] == "status":
                            claim = self.proposals.claims.get(parts[1])
                            if claim is None or (claim.tenant, claim.actor) != (
                                person.tenant_id,
                                person.actor_id,
                            ):
                                raise Refused("Unknown outcome for this employee")
                            await self.check(claim)
                            await ctx.send(
                                f"{claim.delivery.value} {claim.band_id}; receipt {claim.receipt.value}"
                            )
                            return
                        if len(parts) != 3 or parts[0] != "recipient":
                            raise Refused(
                                "Commands: sign in, new code, confirm destination, refresh, status, sign out, recipient ROOM_ID RECIPIENT_ID"
                            )
                        room = next(
                            (room for room in person.rooms if room.id == parts[1]), None
                        )
                        if room is None or parts[2] not in room.recipients:
                            raise Refused("Recipient is outside your fixture")
                        self.current(person.alias).recipients[room.id] = parts[2]
                        await ctx.send(
                            "Recipient selected for future reviews; nothing sent"
                        )
                return
            session = self.current(person.alias)
            room = next(
                (
                    room
                    for room, root in session.roots.items()
                    if activity.get("replyToId") == root
                    or origin.conversation.endswith(";messageid=" + root)
                ),
                None,
            )
            if room is None or not session.ready:
                raise Refused("Reply in one of your active Band room threads")
            await self.roster(person)
            recipient = session.recipients.get(room)
            if recipient is None:
                raise Refused(
                    "Choose a recipient in your personal bot chat, then resubmit"
                )
            await self.teams.stage(ctx, room, recipient)
        except ValueError as error:
            await ctx.send(str(error))

    async def close(self) -> None:
        for alias in tuple(set(self.sessions) | set(self.tasks)):
            await self.signout(alias)
        await self.http.aclose()
