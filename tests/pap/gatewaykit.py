"""Authenticated ASGI gateway peers and a durable Band acceptance double."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx
from bandpap.auth import Access, AuthError, JWTAuthenticator, SecurityStore, SigningKey
from bandpap.discovery import AuthDiscovery, OAuthMetadata
from bandpap.gateway import (
    GatewayConfig,
    GatewayConversation,
    GatewayDelivery,
    GatewayGrant,
    PAPGateway,
)
from bandpap.storage import Scope, Session, StateStore
from bandpap.wire import Message, SendMessage
from cryptography.fernet import Fernet

ORIGIN = "https://service.example"
ISSUER = "https://issuer.example"
RESOURCE = ORIGIN + "/poppy"


class BandPeer:
    def __init__(self) -> None:
        self.revoked: set[str] = set()
        self.accepted: dict[str, GatewayDelivery] = {}
        self.failure = False
        self.project: dict[str, tuple[dict[str, str], ...]] = {}

    def grant(self, access: Access) -> GatewayGrant:
        proxy = "proxy-" + access.session.scope.user
        return GatewayGrant(
            id="grant-" + access.session.scope.user,
            room="one-room",
            proxy=proxy,
            recipients=("company",),
            visible_participants=self.project.get(
                proxy,
                (
                    {"id": proxy, "name": "Personal agent"},
                    {"id": "company", "name": "Company"},
                ),
            ),
        )

    async def authorize(
        self, access: Access, action: str, conversation: GatewayConversation | None
    ) -> GatewayGrant:
        if access.session.session_id in self.revoked:
            raise PermissionError("Live session revoked: private diagnostic")
        if "poppy:read" not in access.scopes:
            raise PermissionError("Missing required scope")
        return self.grant(access)

    async def dispatch(self, work: GatewayDelivery) -> None:
        self.accepted.setdefault(work.id, work)
        if self.failure:
            self.failure = False
            raise TimeoutError("Response lost after durable queue acceptance")


class Evidence:
    def __init__(self, token: object) -> None:
        self.token, self.granted = token, False

    async def evidence(
        self, request: Any, access: Access, operation_id: str, confirmation: Any
    ) -> object:
        return self.token if self.granted else None


class GatewayHarness:
    def __init__(
        self,
        path: Path,
        *,
        band: BandPeer | None = None,
        store: StateStore | None = None,
        page_size: int = 2,
        **options: Any,
    ) -> None:
        self.store = store or StateStore(path)
        self.security = SecurityStore(self.store, encryption_key=Fernet.generate_key())
        self.issuer = SigningKey.generate()
        self.proof = SigningKey.generate()
        self.band = band or BandPeer()
        self.live: dict[str, Access] = {}

        async def resolve(claims: dict[str, Any]) -> Access:
            access = self.live.get(claims["session_id"])
            if access is None or claims["session_id"] in self.band.revoked:
                raise AuthError("invalid_token")
            return access

        auth = JWTAuthenticator(
            issuer=ISSUER,
            audience=RESOURCE,
            keys=[self.issuer.public_jwk() | {"kid": self.issuer.kid}],
            security=self.security,
            resolve=resolve,
        )
        config = GatewayConfig(
            public_url=ORIGIN,
            name="Service",
            auth=AuthDiscovery(issuer=ISSUER),
            oauth_metadata=OAuthMetadata(
                issuer=ISSUER,
                token_endpoint=ISSUER + "/token",
                revocation_endpoint=ISSUER + "/revoke",
                poppy_domains=("service.example",),
                dpop_signing_alg_values_supported=("ES256",),
            ),
            page_size=page_size,
            stream_seconds=0.04,
            poll_seconds=0.005,
        )
        self.gateway = PAPGateway(
            config, store=self.store, authenticator=auth, band=self.band, **options
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.gateway, raise_app_exceptions=False),
            base_url=ORIGIN,
        )

    def access(
        self,
        user: str = "alice",
        *,
        session_id: str | None = None,
        account: str | None = None,
        client: str = "https://agent.example/client.json",
        tenant: str = "tenant",
        scopes: frozenset[str] = frozenset({"poppy:read", "poppy:write"}),
    ) -> Access:
        access = Access(
            session=Session(
                scope=Scope(
                    tenant=tenant,
                    user=user,
                    client=client,
                    issuer=ISSUER,
                    direction="inbound",
                ),
                session_id=session_id or "session-" + user,
                account=account,
            ),
            scopes=scopes,
            extensions={"operations": "1"},
        )
        self.live[access.session.session_id] = access
        return access

    def headers(
        self, access: Access, method: str, path: str, **claims: Any
    ) -> dict[str, str]:
        token = self.issuer.sign(
            {
                "iss": ISSUER,
                "aud": RESOURCE,
                "sub": access.session.scope.user,
                "client_id": access.session.scope.client,
                "session_id": access.session.session_id,
                "account": access.session.account,
                "scope": " ".join(access.scopes),
                "iat": int(time.time()),
                "exp": int(time.time()) + 300,
                "cnf": {"jkt": self.proof.thumbprint()},
                **claims,
            }
        )
        return {
            "Authorization": "DPoP " + token,
            "DPoP": self.proof.proof(method, ORIGIN + path.split("?")[0], token=token),
        }

    async def request(
        self,
        access: Access,
        method: str,
        path: str,
        *,
        value: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        return await self.client.request(
            method,
            path,
            headers=self.headers(access, method, path) | (headers or {}),
            content=json.dumps(value).encode() if value is not None else None,
        )

    async def close(self) -> None:
        await self.client.aclose()
        self.store.close()


def message(
    id_: str = "msg_one", *, sender: str = "agent", **content: Any
) -> dict[str, Any]:
    return {"message": {"id": id_, "sender": sender, **(content or {"text": "hello"})}}


def incoming(id_: str = "msg_one", **content: Any) -> SendMessage:
    return SendMessage(
        message=Message(id=id_, sender="agent", **(content or {"text": "hello"}))
    )


def reply(
    event_id: str = "evt_reply", *, sender: str = "agent", **content: Any
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "message",
        "created_at": "2026-10-16T20:00:00Z",
        "message": {
            "id": "msg_" + event_id,
            "role": "company",
            "sender": sender,
            **(content or {"text": "Company answer"}),
        },
    }
