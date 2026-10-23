"""Offline ASGI/DPoP/Band transport harness; no live platform or model calls."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
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
    GatewayConfig,
    GatewayConversation,
    GatewayDelivery,
    GatewayGrant,
    PAPGateway,
)
from bandpap.http import Response, Transport
from bandpap.oauth import OAuthClient
from bandpap.room import RestRoomSender, RoomDelivery, RoomOutbox
from bandpap.routing import Grant, Grants
from bandpap.storage import Route, Scope, Session, StateStore, StateUnavailable
from bandpap.wire import Message, SendMessage
from bandpap.worker import Connection, Connections, ConversationWorker, stable_id
from cryptography.fernet import Fernet

from tests.pap.adapterkit import incoming, turn
from tests.pap.gatewaykit import GatewayHarness, message, reply

ROOM = "collaboration-room"


@dataclass(frozen=True)
class BandPost:
    id: str
    proxy: str
    room: str
    text: str
    recipients: tuple[str, ...]


class BandTransport:
    """Distinct client objects attest each proxy identity before SDK-shaped POSTs."""

    def __init__(self) -> None:
        self.posts: list[BandPost] = []
        self.identities: list[str] = []

    def client(self, proxy: str) -> Any:
        async def identity(**options: Any) -> Any:
            self.identities.append(proxy)
            return SimpleNamespace(data=SimpleNamespace(id=proxy))

        async def post(room: str, *, message: Any, **options: Any) -> Any:
            identifier = "band_" + str(len(self.posts))
            recipients = tuple(mention.id for mention in message.mentions)
            self.posts.append(
                BandPost(identifier, proxy, room, message.content, recipients)
            )
            return SimpleNamespace(
                data=SimpleNamespace(
                    id=identifier,
                    success=True,
                    recipients=[
                        SimpleNamespace(id=recipient) for recipient in recipients
                    ],
                )
            )

        return SimpleNamespace(
            agent_api_identity=SimpleNamespace(get_agent_me=identity),
            agent_api_messages=SimpleNamespace(create_agent_chat_message=post),
        )


class RoomAuthority:
    def __init__(self) -> None:
        self.routes: dict[str, tuple[str, tuple[str, ...]]] = {}
        self.revoked: set[str] = set()

    async def authorize(self, delivery: RoomDelivery) -> None:
        if (
            delivery.grant in self.revoked
            or delivery.room != ROOM
            or self.routes.get(delivery.grant) != (delivery.proxy, delivery.recipients)
        ):
            raise StateUnavailable("Room route is not authorized")


class GatewayBand:
    def __init__(self, name: str, outbox: RoomOutbox, authority: RoomAuthority) -> None:
        self.grant = GatewayGrant(
            id="inbound-" + name,
            room=ROOM,
            proxy="personal-" + name,
            recipients=("service-" + name,),
            visible_participants=({"id": "service-" + name, "name": name},),
        )
        authority.routes[self.grant.id] = (self.grant.proxy, self.grant.recipients)
        self.authority, self.outbox = authority, outbox
        self.accepted: set[str] = set()

    async def authorize(
        self, access: Access, action: str, conversation: GatewayConversation | None
    ) -> GatewayGrant:
        if self.grant.id in self.authority.revoked:
            raise PermissionError("Gateway grant revoked")
        return self.grant

    async def dispatch(self, work: GatewayDelivery) -> None:
        await self.outbox.accept(
            RoomDelivery(
                id=work.id,
                room=work.grant.room,
                proxy=work.grant.proxy,
                recipients=work.grant.recipients,
                grant=work.grant.id,
                text=(work.message.text if work.message is not None else None)
                or "Structured input",
                record=work.model_dump(mode="json"),
            )
        )
        self.accepted.add(work.id)
        await self.outbox.flush(proxy=work.grant.proxy)


class ASGIChannel(Transport):
    def __init__(self, gateway: PAPGateway) -> None:
        super().__init__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gateway.app, raise_app_exceptions=False)
        )
        self.requests: list[tuple[str, str, dict[str, Any] | None]] = []
        self.lose_first_acceptance = False
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        document: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        self.requests.append((method, url, document))
        if method == "POST" and self.entered is not None:
            self.entered.set()
            assert self.release is not None
            await self.release.wait()
        response = await self.http.request(method, url, headers=headers, json=document)
        if (
            method == "POST"
            and response.status_code == 201
            and self.lose_first_acceptance
        ):
            self.lose_first_acceptance = False
            raise TimeoutError("First acceptance response lost after gateway commit")
        return Response(response.status_code, dict(response.headers), response.content)


@dataclass
class Company:
    name: str
    session: Session
    access: Access
    oauth: OAuthClient
    channel: ASGIChannel
    gateway: PAPGateway
    gateway_store: StateStore
    band: GatewayBand
    worker: ConversationWorker
    adapter: PAPAdapter
    grant: Grant


class Roundtrip:
    def __init__(self, path: Path) -> None:
        self.path, self.store = path, StateStore(path / "personal.db")
        self.grants, self.connections = Grants(self.store), Connections()
        self.authority, self.band = RoomAuthority(), BandTransport()
        proxies = ("company-a", "company-b", "personal-a", "personal-b")
        sender = RestRoomSender(
            {proxy: self.band.client(proxy) for proxy in proxies},
            authorize=self.authority.authorize,
        )
        self.sender = sender
        self.outbox = RoomOutbox(
            self.store, sender=sender, authorize=self.authority.authorize
        )
        self.companies = {name: self._company(name) for name in ("a", "b")}

    def _company(self, name: str) -> Company:
        origin, issuer = (
            "https://" + name + ".company.example",
            "https://" + name + ".issuer.example",
        )
        resource = origin + "/poppy"
        metadata = OAuthMetadata(
            issuer=issuer,
            token_endpoint=issuer + "/token",
            revocation_endpoint=issuer + "/revoke",
            poppy_domains=(name + ".company.example",),
            dpop_signing_alg_values_supported=("ES256",),
        )
        owner = Session(
            scope=Scope(
                tenant="tenant",
                user="pairwise-" + name,
                client="https://agent.example/client.json",
                issuer=issuer,
                direction="outbound",
            ),
            session_id="session-" + name,
        )
        access = Access(
            session=owner.model_copy(
                update={
                    "scope": owner.scope.model_copy(update={"direction": "inbound"})
                }
            )
        )
        gateway_store = StateStore(self.path / (name + "-gateway.db"))
        security = SecurityStore(gateway_store, encryption_key=Fernet.generate_key())
        issuer_key = SigningKey.generate()
        band = GatewayBand(name, self.outbox, self.authority)

        async def resolve(claims: dict[str, Any]) -> Access:
            if (
                claims["session_id"] != access.session.session_id
                or band.grant.id in self.authority.revoked
            ):
                raise AuthError("invalid_token")
            return access

        authenticator = JWTAuthenticator(
            issuer=issuer,
            audience=resource,
            keys=[issuer_key.public_jwk() | {"kid": issuer_key.kid}],
            security=security,
            resolve=resolve,
        )
        gateway = PAPGateway(
            GatewayConfig(
                public_url=origin,
                name=name,
                auth=AuthDiscovery(issuer=issuer),
                oauth_metadata=metadata,
                page_size=100,
            ),
            store=gateway_store,
            authenticator=authenticator,
            band=band,
        )
        document = DiscoveryDocument.model_validate(
            {
                "protocol_version": "0.1",
                "organization": {"name": name, "domain": name + ".company.example"},
                "auth": {"issuer": issuer},
                "agent": {
                    "protocols": [
                        {
                            "type": "poppy",
                            "endpoint": origin + "/poppy/conversations",
                            "resource": resource,
                        }
                    ]
                },
            }
        )
        channel = ASGIChannel(gateway)
        oauth = OAuthClient(
            discovery=VerifiedDiscovery(
                document, metadata, origin + "/.well-known/poppy.json"
            ),
            scope=owner.scope,
            connection="connection-" + name,
            identity=SigningKey.generate(),
            security=SecurityStore(self.store, encryption_key=Fernet.generate_key()),
            transport=channel,
        )
        version, record = oauth.record()
        proof = SigningKey.restore(record["key"])
        token = issuer_key.sign(
            {
                "iss": issuer,
                "aud": resource,
                "sub": owner.scope.user,
                "client_id": owner.scope.client,
                "session_id": owner.session_id,
                "scope": "",
                "iat": int(time.time()),
                "exp": int(time.time()) + 600,
                "cnf": {"jkt": proof.thumbprint()},
            }
        )
        record.update(
            {
                "session": owner.session_id,
                "signed_in": False,
                "tokens": {
                    "DPoP:" + resource: {
                        "access": token,
                        "expires": time.time() + 600,
                        "scope": "",
                    }
                },
            }
        )
        oauth.security.put(owner.scope, oauth.name, record, expected=version)
        grant = Grant(
            id="outbound-" + name,
            session=owner,
            connection=oauth.connection,
            binding="thread-" + name,
            route=Route(
                room=ROOM,
                proxy="company-" + name,
                source="muse",
                recipient="muse",
                grant="outbound-" + name,
            ),
            sender_type="Agent",
            visible_participants=("muse", "company-" + name),
        )
        self.grants.provision(grant)
        self.authority.routes[grant.id] = (grant.route.proxy, (grant.route.recipient,))

        async def current_session() -> Session:
            return owner

        self.connections.add(
            grant, Connection(oauth=oauth, current_session=current_session)
        )
        worker = ConversationWorker(
            grants=self.grants,
            connections=self.connections,
            outbox=self.outbox,
            proxy=grant.route.proxy,
        )
        adapter = PAPAdapter(
            PAPAdapterConfig(proxy_id=grant.route.proxy),
            grants=self.grants,
            worker=worker,
        )
        return Company(
            name,
            owner,
            access,
            oauth,
            channel,
            gateway,
            gateway_store,
            band,
            worker,
            adapter,
            grant,
        )

    async def ingest(
        self, name: str, text: str = "A structured collaboration question"
    ) -> None:
        company = self.companies[name]
        tools = turn()
        await company.adapter.on_message(
            incoming(
                id="request-" + name,
                room_id=ROOM,
                content=text,
                metadata={
                    "mentions": [{"id": company.grant.route.proxy, "kind": "mention"}]
                },
            ),
            tools,
            [],
            None,
            None,
            is_session_bootstrap=True,
            room_id=ROOM,
        )
        tools.turn.settle.assert_called_once()

    async def answer(self, name: str, *, text: str = "Company answer") -> str:
        company = self.companies[name]
        remote_id = self.store.conversation(
            company.session, company.grant.binding
        ).remote_id
        assert remote_id is not None
        await company.gateway.publish(
            company.access,
            remote_id,
            reply("evt_" + name, text=text, data={"private": name}),
            source_id="service-" + name,
            source_type="Agent",
            recipients=(company.band.grant.proxy,),
        )
        self.store.db.execute(
            "UPDATE pap_worker_state SET next_poll=0 WHERE grant_id=?",
            (company.grant.id,),
        )
        await company.worker.tick(company.grant.id)
        return remote_id

    async def bridge_roundtrip(self) -> dict[str, Any]:
        central = GatewayHarness(self.path / "central-gateway.db", page_size=100)
        external = central.access()
        central_proxy = "inbound-personal"
        central_grant = GatewayGrant(
            id="central-grant",
            room=ROOM,
            proxy=central_proxy,
            recipients=("company-a",),
            visible_participants=({"id": "company-a", "name": "Company A"},),
        )
        self.authority.routes[central_grant.id] = (
            central_proxy,
            central_grant.recipients,
        )
        self.sender.clients[central_proxy] = self.band.client(central_proxy)

        async def authorize(
            access: Access, action: str, conversation: GatewayConversation | None
        ) -> GatewayGrant:
            if (
                access.session != external.session
                or central_grant.id in self.authority.revoked
            ):
                raise PermissionError("External caller has no room grant")
            return central_grant

        central.gateway.band = GatewayBridge(outbox=self.outbox, authorize=authorize)
        try:
            accepted = await central.request(
                external,
                "POST",
                "/poppy/conversations",
                value=message(
                    "external-first",
                    text="Ask Company A",
                    data={"request": "private-input"},
                ),
            )
            assert accepted.status_code == 201
            inbound_id = accepted.json()["conversation_id"]
            await central.gateway.flush()
            await self.outbox.flush(proxy=central_proxy)
            inbound_post = next(
                post for post in self.band.posts if post.proxy == central_proxy
            )
            inbound_ref = inbound_post.text.split("[PAP record: ")[1].removesuffix("]")
            original = await self.outbox.record(
                inbound_ref, room=ROOM, actor="company-a"
            )

            company = self.companies["a"]
            chained = Grant(
                id="chained-a",
                session=company.session,
                connection="chained-connection-a",
                binding="chained-thread-a",
                route=Route(
                    room=ROOM,
                    proxy="company-a",
                    source=central_proxy,
                    recipient=central_proxy,
                    grant="chained-a",
                ),
                sender_type="Agent",
            )
            self.grants.provision(chained)
            self.authority.routes[chained.id] = ("company-a", (central_proxy,))
            oauth = OAuthClient(
                discovery=company.oauth.discovery,
                scope=company.oauth.scope,
                connection=chained.connection,
                identity=company.oauth.identity,
                security=company.oauth.security,
                transport=company.channel,
            )
            version, _ = oauth.record()
            oauth.security.put(
                oauth.scope, oauth.name, company.oauth.record()[1], expected=version
            )

            async def current_session() -> Session:
                return company.session

            self.connections.add(
                chained, Connection(oauth=oauth, current_session=current_session)
            )
            structured = original["message"]
            self.grants.stage(
                chained.id,
                SendMessage(
                    message=Message(
                        id=stable_id(inbound_post.id),
                        sender="agent",
                        text=structured["text"],
                        data=structured.get("data"),
                    )
                ),
            )
            tools = turn()
            await company.adapter.on_message(
                incoming(
                    id=inbound_post.id,
                    room_id=ROOM,
                    sender_id=central_proxy,
                    content=inbound_post.text,
                    metadata={"mentions": [{"id": "company-a", "kind": "mention"}]},
                ),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id=ROOM,
            )
            tools.turn.settle.assert_called_once()
            await company.worker.tick(chained.id)
            remote_id = self.store.conversation(
                company.session, chained.binding
            ).remote_id
            assert remote_id is not None
            await company.gateway.flush()
            await company.gateway.publish(
                company.access,
                remote_id,
                reply(
                    "chain-answer",
                    text="Company A answered through the room",
                    data={"answer": "private-output"},
                ),
                source_id="service-a",
                source_type="Agent",
                recipients=(company.band.grant.proxy,),
            )
            self.store.db.execute(
                "UPDATE pap_worker_state SET next_poll=0 WHERE grant_id=?",
                (chained.id,),
            )
            await company.worker.tick(chained.id)
            reply_post = next(
                post
                for post in reversed(self.band.posts)
                if post.proxy == "company-a"
                and post.recipients == (central_proxy,)
                and "Company A answered" in post.text
            )
            reply_ref = reply_post.text.split("[PAP record: ")[1].removesuffix("]")
            private_reply = await self.outbox.record(
                reply_ref, room=ROOM, actor=central_proxy
            )
            correlations = GatewayReplies(self.store)
            correlations.stage(
                ReplyBinding(
                    access=external,
                    conversation_id=inbound_id,
                    room=ROOM,
                    proxy=central_proxy,
                    source="company-a",
                    message_id=reply_post.id,
                    data=private_reply["message"]["data"],
                )
            )
            adapter = PAPGatewayAdapter(
                PAPGatewayAdapterConfig(proxy_id=central_proxy),
                gateway=central.gateway,
                replies=correlations,
            )
            tools = turn()
            await adapter.on_message(
                incoming(
                    id=reply_post.id,
                    room_id=ROOM,
                    sender_id="company-a",
                    content=reply_post.text,
                    metadata={"mentions": [{"id": central_proxy, "kind": "mention"}]},
                ),
                tools,
                [],
                None,
                None,
                is_session_bootstrap=True,
                room_id=ROOM,
            )
            tools.turn.settle.assert_called_once()
            result = await central.request(
                external, "GET", "/poppy/conversations/" + inbound_id + "/events"
            )
            assert result.status_code == 200
            return result.json()
        finally:
            await central.close()

    async def close(self) -> None:
        for company in self.companies.values():
            await company.channel.http.aclose()
            company.gateway_store.close()
        self.store.close()
