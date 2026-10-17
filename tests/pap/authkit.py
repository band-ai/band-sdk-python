"""Deterministic OAuth credentials and scripted protocol peers."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from bandpap.auth import SecurityStore, SigningKey
from bandpap.discovery import DiscoveryDocument, OAuthMetadata
from bandpap.fetch import JsonResponse, VerifiedDiscovery
from bandpap.http import Response, Transport
from bandpap.oauth import OAuthClient
from bandpap.storage import Scope, StateStore
from cryptography.fernet import Fernet


def response(status_code: int = 200, **value: Any) -> Response:
    return Response(status_code, {}, json.dumps(value).encode())


def tokens(
    *,
    signed: bool = False,
    scopes: str = "",
    access: str = "access",
    refresh: str | None = None,
) -> Response:
    body = {
        "access_token": access,
        "token_type": "DPoP",
        "expires_in": 3600,
        "scope": scopes,
        "session_id": "session",
        "signed_in": signed,
    }
    if refresh:
        body["refresh_token"] = refresh
    return response(**body)


class ScriptedTransport(Transport):
    def __init__(
        self, *replies: Response | Exception | Callable[[], Awaitable[Response]]
    ) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, **kwargs: Any) -> Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return await reply() if callable(reply) else reply


def discovery() -> VerifiedDiscovery:
    issuer = "https://company.example"
    scope = ["poppy:read", "poppy:write"]
    doc = DiscoveryDocument.model_validate(
        {
            "protocol_version": "0.1",
            "organization": {"name": "Company", "domain": "company.example"},
            "auth": {
                "issuer": issuer,
                "direct": {"scopes": scope},
                "device": {"scopes": scope},
                "mediated": {
                    "scopes": scope,
                    "endpoint": issuer + "/signin",
                    "fields": [
                        {"name": "password", "label": "Password", "secret": True}
                    ],
                },
            },
            "agent": {
                "protocols": [{"type": "poppy", "endpoint": issuer + "/conversations"}]
            },
            "apis": [{"type": "mcp", "url": issuer + "/mcp", "description": "Tools"}],
        }
    )
    meta = OAuthMetadata.model_validate(
        {
            "issuer": issuer,
            "token_endpoint": issuer + "/token",
            "revocation_endpoint": issuer + "/revoke",
            "authorization_endpoint": issuer + "/authorize",
            "device_authorization_endpoint": issuer + "/device",
            "poppy_domains": ["company.example"],
            "dpop_signing_alg_values_supported": ["ES256"],
        }
    )
    return VerifiedDiscovery(doc, meta, issuer + "/.well-known/poppy.json")


def client(
    transport: ScriptedTransport,
    *,
    path: Path | str = ":memory:",
    encryption_key: bytes | None = None,
) -> OAuthClient:
    store = StateStore(path)
    security = SecurityStore(
        store, encryption_key=encryption_key or Fernet.generate_key()
    )
    scope = Scope(
        tenant="tenant",
        user="user",
        client="https://agent.example/client.json",
        issuer="https://company.example",
        direction="outbound",
    )
    return OAuthClient(
        discovery=discovery(),
        scope=scope,
        connection="company",
        identity=SigningKey.generate(),
        security=security,
        transport=transport,
    )


def install_refresh(oauth: OAuthClient, *, refresh: str = "refresh") -> None:
    version, record = oauth.record()
    record.update(
        {
            "refresh": refresh,
            "refresh_scopes": ["poppy:read"],
            "session": "session",
            "signed_in": True,
        }
    )
    oauth.security.put(oauth.scope, oauth.name, record, expected=version)


class MetadataFetcher:
    def __init__(self, documents: dict[str, dict[str, Any]]) -> None:
        self.documents = documents

    async def get_json(self, url: str) -> JsonResponse:
        return JsonResponse(url, self.documents[url])
