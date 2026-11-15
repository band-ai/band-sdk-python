# /// script
# requires-python = ">=3.11"
# dependencies = ["band-pap>=0.1.0.dev0", "httpx>=0.28,<1"]
# ///
"""A worked, ephemeral PAP host factory with fixed demo authorization policies."""

from __future__ import annotations

import asyncio
import secrets
import tempfile
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from bandpap.adapter import PAPAdapter, PAPAdapterConfig
from bandpap.auth import Access, AuthError, JWTAuthenticator, SecurityStore, SigningKey
from bandpap.bridge import (
    GatewayBridge,
    GatewayReplies,
    PAPGatewayAdapter,
    PAPGatewayAdapterConfig,
    ReplyBinding,
)
from bandpap.discovery import AuthDiscovery, DiscoveryDocument, OAuthMetadata
from bandpap.fetch import VerifiedDiscovery
from bandpap.gateway import (
    Action,
    GatewayConfig,
    GatewayConversation,
    GatewayDelivery,
    GatewayGrant,
    PAPGateway,
)
from bandpap.oauth import OAuthClient
from bandpap.room import RoomDelivery, RoomOutbox
from bandpap.routing import Grant, Grants
from bandpap.storage import Route, Scope, Session, StateStore, StateUnavailable
from bandpap.wire import Message, MessageEvent, SendMessage, StateEvent, parse_event
from bandpap.worker import Connection, Connections, ConversationWorker, stable_id
from cryptography.fernet import Fernet
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.routing import Route as HTTPRoute

from band.core.types import PlatformMessage
from examples.pap.simulation import (
    ROOM,
    Identity,
    LocalHTTPS,
    RoomSender,
    SimulatedRoom,
    plain_text,
    receive,
    reference,
)

PEERS = ("company-a", "company-b")
CLIENT = "https://personal.example.invalid/client.json"
CAPABILITIES = {
    "company-a": "Document review and shared planning",
    "company-b": "Support triage and handoff updates",
}


class Site:
    """A real PAP gateway whose trust registry contains only fixed demo sessions."""

    def __init__(self, host: DemoHost, name: str) -> None:
        self.host, self.origin = host, "https://" + name + ".example.invalid"
        self.store = host.store(name)
        self.security = SecurityStore(self.store, encryption_key=host.encryption_key)
        self.signing_key = SigningKey.generate()
        self.registered: dict[str, tuple[Access, GatewayGrant]] = {}
        self.proof_keys: dict[str, SigningKey] = {}
        self.revoked: set[str] = set()
        self.metadata = OAuthMetadata(
            issuer=self.origin,
            token_endpoint=self.origin + "/token",
            revocation_endpoint=self.origin + "/revoke",
            poppy_domains=(name + ".example.invalid",),
            dpop_signing_alg_values_supported=("ES256",),
        )
        authenticator = JWTAuthenticator(
            issuer=self.origin,
            audience=self.origin + "/poppy",
            keys=[self.signing_key.public_jwk() | {"kid": self.signing_key.kid}],
            security=self.security,
            resolve=self.resolve,
        )
        self.bridge = GatewayBridge(outbox=host.outbox, authorize=self.authorize)
        self.gateway = PAPGateway(
            GatewayConfig(
                public_url=self.origin,
                name=name,
                auth=AuthDiscovery(issuer=self.origin),
                oauth_metadata=self.metadata,
                page_size=100,
            ),
            store=self.store,
            authenticator=authenticator,
            band=self.bridge,
        )
        self.http = LocalHTTPS(self.gateway.app, self.origin)
        host.clients.append(self.http)

    def register(self, user: str, grant: GatewayGrant) -> Access:
        access = Access(
            session=Session(
                scope=Scope(
                    tenant="offline-demo",
                    user=user,
                    client=CLIENT,
                    issuer=self.origin,
                    direction="inbound",
                ),
                session_id="session_" + secrets.token_hex(16),
            )
        )
        self.registered[access.session.session_id] = access, grant
        self.proof_keys[access.session.session_id] = SigningKey.generate()
        self.host.routes[grant.id] = grant
        self.host.route_owners[grant.id] = self, access
        return access

    def live(self, access: Access) -> GatewayGrant:
        registered = self.registered.get(access.session.session_id)
        if (
            registered is None
            or registered[0] != access
            or access.session.session_id in self.revoked
        ):
            raise AuthError("invalid_token")
        return registered[1]

    async def resolve(self, claims: dict[str, Any]) -> Access:
        registered = self.registered.get(claims.get("session_id", ""))
        if registered is None:
            raise AuthError("invalid_token")
        self.live(registered[0])
        return registered[0]

    async def authorize(
        self, access: Access, action: Action, conversation: GatewayConversation | None
    ) -> GatewayGrant:
        grant = self.live(access)
        if action not in {
            "create",
            "send",
            "read",
            "handoff",
            "close",
            "dispatch",
            "publish",
            "roster",
        }:
            raise PermissionError("The demo grants no account or business permissions")
        if conversation is not None and (
            conversation.grant.binding() != grant.binding()
            or conversation.owner.scope != access.session.scope
        ):
            raise PermissionError(
                "This demo session cannot select another company's route"
            )
        return grant

    def token(self, access: Access, proof: SigningKey) -> str:
        self.live(access)
        return self.signing_key.sign(
            {
                "iss": self.origin,
                "aud": self.origin + "/poppy",
                "sub": access.session.scope.user,
                "client_id": CLIENT,
                "session_id": access.session.session_id,
                "scope": "",
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
                "cnf": {"jkt": proof.thumbprint()},
            }
        )

    async def request(
        self,
        access: Access,
        method: str,
        path: str,
        document: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.live(access)
        proof = self.proof_keys[access.session.session_id]
        token = self.token(access, proof)
        url = self.origin + path
        response = await self.http.request(
            method,
            url,
            headers={
                "Authorization": "DPoP " + token,
                "DPoP": proof.proof(method, url, token=token),
            },
            document=document,
        )
        if response.status not in {200, 201, 202}:
            raise RuntimeError("The offline PAP request was rejected")
        return response.json()


class Company:
    """Example-owned deterministic responder; it never invokes a model."""

    def __init__(self, host: DemoHost, name: str) -> None:
        self.host, self.name = host, name
        self.proxy, self.personal = name, "personal-" + name
        self.remote, self.staff = "remote-" + name, "staff-" + name
        self.site = Site(host, name)
        remote_grant = GatewayGrant(
            id="remote-grant-" + name,
            room=ROOM,
            proxy=self.remote,
            recipients=(self.staff,),
            visible_participants=({"id": self.staff, "name": name},),
        )
        self.access = self.site.register(
            "pairwise-" + secrets.token_hex(12), remote_grant
        )
        owner = self.access.session.model_copy(
            update={
                "scope": self.access.session.scope.model_copy(
                    update={"direction": "outbound"}
                )
            }
        )
        self.owner = owner
        document = DiscoveryDocument.model_validate(
            {
                "protocol_version": "0.1",
                "organization": {"name": name, "domain": name + ".example.invalid"},
                "auth": {"issuer": self.site.origin},
                "agent": {
                    "protocols": [
                        {
                            "type": "poppy",
                            "endpoint": self.site.origin + "/poppy/conversations",
                            "resource": self.site.origin + "/poppy",
                        }
                    ]
                },
            }
        )
        oauth = OAuthClient(
            discovery=VerifiedDiscovery(
                document,
                self.site.metadata,
                self.site.origin + "/.well-known/poppy.json",
            ),
            scope=owner.scope,
            connection="connection-" + name,
            identity=SigningKey.generate(),
            security=SecurityStore(host.state, encryption_key=host.encryption_key),
            transport=self.site.http,
        )
        version, credentials = oauth.record()
        proof = SigningKey.restore(credentials["key"])
        credentials.update(
            {
                "session": owner.session_id,
                "signed_in": False,
                "tokens": {
                    "DPoP:" + self.site.origin + "/poppy": {
                        "access": self.site.token(self.access, proof),
                        "expires": time.time() + 600,
                        "scope": "",
                    }
                },
            }
        )
        oauth.security.put(owner.scope, oauth.name, credentials, expected=version)
        self.grant = Grant(
            id="outbound-grant-" + name,
            session=owner,
            connection=oauth.connection,
            binding="conversation-" + name,
            route=Route(
                room=ROOM,
                proxy=self.proxy,
                source=self.personal,
                recipient=self.personal,
                grant="outbound-grant-" + name,
            ),
            sender_type="Agent",
            visible_participants=(self.personal, self.proxy),
        )
        host.grants.provision(self.grant)
        host.route_owners[self.grant.id] = self.site, self.access

        async def current_session() -> Session:
            self.site.live(self.access)
            source = host.source_owners.get(self.grant.id)
            if source is not None:
                source_site, source_access = source
                source_site.live(source_access)
            return owner

        host.connections.add(
            self.grant, Connection(oauth=oauth, current_session=current_session)
        )
        self.worker = ConversationWorker(
            grants=host.grants,
            connections=host.connections,
            outbox=host.outbox,
            proxy=self.proxy,
        )
        self.adapter = PAPAdapter(
            PAPAdapterConfig(proxy_id=self.proxy),
            grants=host.grants,
            worker=self.worker,
        )
        self.answered: set[str] = set()

    async def forward(self, message: PlatformMessage) -> None:
        record = await self.host.outbox.record(
            reference(message), room=ROOM, actor=self.proxy
        )
        original = GatewayDelivery.model_validate(record)
        expected = self.host.inbound[self.name]
        if (
            original.owner != self.host.accesses[self.name].session
            or original.grant.binding() != expected.binding()
        ):
            raise PermissionError(
                "The addressed input has the wrong private record owner"
            )
        if original.kind != "message" or original.message is None:
            raise ValueError("The demo forwards message handoffs only")
        self.host.conversations[self.name] = original.conversation_id
        self.host.grants.stage(
            self.grant.id,
            SendMessage(
                message=Message.model_validate(
                    original.message.wire()
                    | {"id": stable_id(message.id), "sender": "agent"}
                )
            ),
        )
        await receive(self.adapter, message)

    async def answer(self, message: PlatformMessage) -> None:
        record = await self.host.outbox.record(
            reference(message), room=ROOM, actor=self.staff
        )
        work = GatewayDelivery.model_validate(record)
        if (
            work.owner != self.access.session
            or work.grant.binding() != self.site.live(self.access).binding()
            or work.message is None
        ):
            raise PermissionError("The company received another session's record")
        if work.id in self.answered:
            return
        event = MessageEvent.model_validate(
            {
                "id": stable_id(work.id, "answer"),
                "type": "message",
                "created_at": message.created_at.isoformat(),
                "message": {
                    "id": stable_id(work.id, "reply"),
                    "role": "company",
                    "sender": "agent",
                    "text": self.name
                    + ": "
                    + CAPABILITIES[self.name]
                    + ". Ready to collaborate.",
                    "data": {
                        "peer": self.name,
                        "capability": CAPABILITIES[self.name],
                        "private_note": secrets.token_urlsafe(16),
                    },
                },
            }
        )
        await self.site.gateway.publish(
            self.access,
            work.conversation_id,
            event,
            source_id=self.staff,
            source_type="Agent",
            recipients=(self.remote,),
        )
        self.answered.add(work.id)


class DemoHost:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="band-pap-demo-")
        self.path = Path(self.temporary.name)
        self.closed = False
        self.clients: list[LocalHTTPS] = []
        self.stores: list[StateStore] = []
        self.encryption_key = Fernet.generate_key()
        self.routes: dict[str, GatewayGrant] = {}
        self.route_owners: dict[str, tuple[Site, Access]] = {}
        self.source_owners: dict[str, tuple[Site, Access]] = {}
        self.revoked_routes: set[str] = set()
        self.record_references: dict[str, str] = {}
        self.conversations: dict[str, str] = {}
        self.outcomes: dict[str, dict[str, Any]] = {}
        self.run_lock = asyncio.Lock()
        self.result: dict[str, Any] | None = None

    def store(self, name: str) -> StateStore:
        store = StateStore(self.path / (name + ".sqlite3"))
        self.stores.append(store)
        return store

    async def open(self) -> None:
        self.state = self.store("room")
        self.grants, self.connections = Grants(self.state), Connections()
        self.room, self.identities = SimulatedRoom(self.authorize_room), {}
        self.outbox = RoomOutbox(
            self.state,
            sender=RoomSender(self.room, self.identities),
            authorize=self.authorize_room,
        )
        self.central = Site(self, "collaboration")
        self.gateway = self.central.gateway
        self.replies = GatewayReplies(self.state)
        self.accesses: dict[str, Access] = {}
        self.inbound: dict[str, GatewayGrant] = {}
        self.peers: dict[str, Company] = {}
        self.adapters: dict[str, PAPGatewayAdapter] = {}
        for name in PEERS:
            peer = Company(self, name)
            grant = GatewayGrant(
                id="inbound-grant-" + name,
                room=ROOM,
                proxy=peer.personal,
                recipients=(peer.proxy,),
                visible_participants=(
                    {"id": peer.personal, "name": "Personal agent"},
                    {"id": peer.proxy, "name": name},
                ),
            )
            self.inbound[name] = grant
            self.accesses[name] = self.central.register("demo-owner", grant)
            source = self.central, self.accesses[name]
            self.source_owners[peer.grant.id] = source
            self.source_owners[peer.site.live(peer.access).id] = source
            self.peers[name] = peer
            adapter = PAPGatewayAdapter(
                PAPGatewayAdapterConfig(proxy_id=peer.personal),
                gateway=self.gateway,
                replies=self.replies,
            )
            self.adapters[name] = adapter
            self.identities[peer.proxy] = self.room.register(peer.proxy, peer.forward)
            self.identities[peer.personal] = self.room.register(
                peer.personal, self.reply_handler(name)
            )
            self.identities[peer.staff] = self.room.register(peer.staff, peer.answer)
            self.identities[peer.remote] = self.room.register(
                peer.remote, self.reject_unbound
            )

    async def reject_unbound(self, message: PlatformMessage) -> None:
        raise PermissionError(
            "A remote company proxy accepts replies only through its gateway"
        )

    def reply_handler(self, name: str) -> Callable[[PlatformMessage], Awaitable[None]]:
        async def publish(message: PlatformMessage) -> None:
            peer = self.peers[name]
            if message.sender_id != peer.proxy:
                raise PermissionError(
                    "The personal proxy received an unauthorized source"
                )
            record_ref = reference(message)
            private = await self.outbox.record(
                record_ref, room=ROOM, actor=peer.personal
            )
            event = parse_event(private)
            if isinstance(event, StateEvent):
                await self.gateway.publish(
                    self.accesses[name],
                    self.conversations[name],
                    event,
                    source_id=message.sender_id,
                    source_type="Agent",
                    recipients=(peer.personal,),
                )
                return
            if not isinstance(event, MessageEvent):
                raise ValueError("The demo does not advertise other event projections")
            if event.message.data is None or event.message.data.get("peer") != name:
                raise PermissionError(
                    "The company reply has the wrong private record scope"
                )
            self.record_references[name] = record_ref
            public = {"peer": name, "capability": event.message.data["capability"]}
            self.replies.stage(
                ReplyBinding(
                    access=self.accesses[name],
                    conversation_id=self.conversations[name],
                    room=ROOM,
                    proxy=peer.personal,
                    source=peer.proxy,
                    message_id=message.id,
                    data=public,
                )
            )
            await receive(self.adapters[name], message)

        return publish

    async def authorize_room(self, delivery: RoomDelivery) -> None:
        if delivery.grant in self.revoked_routes or delivery.room != ROOM:
            raise PermissionError("The demo route is unavailable")
        owner = self.route_owners.get(delivery.grant)
        if owner is None:
            raise PermissionError("The demo delivery has no registered session owner")
        site, access = owner
        site.live(access)
        source = self.source_owners.get(delivery.grant)
        if source is not None:
            source_site, source_access = source
            source_site.live(source_access)
        route = self.routes.get(delivery.grant)
        if route is not None:
            if (delivery.proxy, delivery.recipients) != (route.proxy, route.recipients):
                raise PermissionError(
                    "The gateway handoff changed its registered recipients"
                )
            return
        grant = self.grants.get(delivery.grant)
        if (delivery.proxy, delivery.recipients) != (
            grant.route.proxy,
            (grant.route.recipient,),
        ):
            raise PermissionError(
                "The company handoff changed its registered recipients"
            )

    async def request(self, name: str) -> None:
        if name not in self.peers:
            raise PermissionError("This demo has no grant for that company")
        await self.central.request(
            self.accesses[name],
            "POST",
            "/poppy/conversations",
            {
                "message": {
                    "id": "request-" + name,
                    "sender": "agent",
                    "text": "Please report your collaboration capabilities.",
                    "data": {
                        "request_for": name,
                        "private_context": secrets.token_urlsafe(16),
                    },
                }
            },
        )

    async def exchange(self, name: str) -> None:
        peer = self.peers[name]
        await self.gateway.flush()
        await self.outbox.flush(proxy=peer.personal)
        await peer.worker.tick(peer.grant.id)
        await peer.site.gateway.flush(peer.access)
        await self.outbox.flush(proxy=peer.remote)
        oauth, session, endpoint, resource = await peer.worker.session(peer.grant)
        await peer.worker.poll(peer.grant, oauth, session, endpoint, resource)
        await peer.worker.project(peer.grant, session)
        await self.outbox.flush(proxy=peer.proxy)
        page = await self.central.request(
            self.accesses[name],
            "GET",
            "/poppy/conversations/" + self.conversations[name] + "/events",
        )
        replies = [
            event["message"]
            for event in page["events"]
            if event["type"] == "message" and event["message"]["role"] == "company"
        ]
        if len(replies) != 1 or replies[0].get("data", {}).get("peer") != name:
            raise RuntimeError(
                "The explicit reply binding did not return the company answer"
            )
        self.outcomes[name] = {
            "reply": plain_text(replies[0]["text"]),
            "reply_bound": True,
            "structured_reply": replies[0]["data"]
            == {"peer": name, "capability": CAPABILITIES[name]},
        }

    async def denials(self) -> dict[str, bool]:
        refused: dict[str, bool] = {}
        peer = self.peers[PEERS[0]]
        delivery = RoomDelivery(
            id="forged",
            room=ROOM,
            proxy=peer.proxy,
            recipients=(peer.personal,),
            grant=peer.grant.id,
            text="forged sender",
        )
        try:
            await self.room.post(
                Identity(
                    participant=peer.proxy,
                    credential=SecretStr(secrets.token_urlsafe(32)),
                ),
                delivery,
            )
        except PermissionError:
            refused["forged_identity"] = True
        try:
            await self.outbox.record(
                self.record_references[PEERS[0]],
                room=ROOM,
                actor=self.peers[PEERS[1]].personal,
            )
        except StateUnavailable:
            refused["cross_peer_record"] = True
        try:
            await self.outbox.accept(
                delivery.model_copy(
                    update={"recipients": (self.peers[PEERS[1]].personal,)}
                )
            )
        except PermissionError:
            refused["cross_peer_delivery"] = True
        unregistered = self.accesses[PEERS[0]].model_copy(
            update={
                "session": self.accesses[PEERS[0]].session.model_copy(
                    update={"session_id": "not-registered"}
                )
            }
        )
        try:
            await self.gateway.read(unregistered, self.conversations[PEERS[0]])
        except AuthError:
            refused["unregistered_session"] = True
        if len(refused) != 4:
            raise RuntimeError("A demo authorization boundary failed")
        return refused

    async def run(self) -> dict[str, Any]:
        async with self.run_lock:
            if self.result is None:
                for phase in (self.request, self.exchange):
                    async with asyncio.TaskGroup() as tasks:
                        for name in PEERS:
                            tasks.create_task(phase(name))
                self.result = {
                    "transport": "Simulated Band room and in-process ASGI; real PAP, JWT, DPoP and SQLite",
                    "room": ROOM,
                    "transcript": self.room.transcript(),
                    "peers": self.outcomes,
                    "denials": await self.denials(),
                    "evidence": self.evidence(),
                }
            return self.result

    def evidence(self) -> dict[str, Any]:
        health = {
            participant: self.outbox.health(proxy=participant)
            for participant in self.identities
        }
        ready = sum(counts["ready"] for counts in health.values())
        uncertain = sum(counts["uncertain"] for counts in health.values())
        if ready or uncertain:
            raise RuntimeError("The offline example has unfinished room deliveries")
        return {
            "company_connections": len(self.peers),
            "outbox": health,
            "outbox_ready": ready,
            "outbox_uncertain": uncertain,
        }

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        async with AsyncExitStack() as resources:
            resources.callback(self.temporary.cleanup)
            for store in self.stores:
                resources.callback(store.close)
            for client in self.clients:
                resources.push_async_callback(client.close)


def build() -> Starlette:
    """ASGI factory; all demo state and signing keys live only for one lifespan."""

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        host = DemoHost()
        app.state.demo = host
        try:
            await host.open()
            yield
        finally:
            await host.close()

    async def run(request: Request) -> JSONResponse:
        return JSONResponse(await request.app.state.demo.run())

    async def gateway(scope: Any, receive_: Any, send: Any) -> None:
        await app.state.demo.gateway(scope, receive_, send)

    app = Starlette(
        routes=[HTTPRoute("/demo/run", run, methods=["POST"]), Mount("/", app=gateway)],
        lifespan=lifespan,
    )
    return app
