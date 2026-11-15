"""Actual OAuth request projection through a deterministic channel transport."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from bandpap.auth import SecurityStore, SigningKey
from bandpap.discovery import DiscoveryDocument, OAuthMetadata
from bandpap.fetch import VerifiedDiscovery
from bandpap.http import Response, Transport
from bandpap.oauth import OAuthClient
from bandpap.storage import StateStore
from cryptography.fernet import Fernet

from tests.pap.kit import session


class ChannelTransport(Transport):
    def __init__(self) -> None:
        super().__init__()
        self.responses: deque[Response | dict[str, Any] | Exception] = deque()
        self.requests: list[dict[str, Any]] = []

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        document: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
    ) -> Response:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers or {},
                "document": document,
                "form": form,
            }
        )
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        if isinstance(result, Response):
            return result
        if document and "id" in document and document.get("jsonrpc") == "2.0":
            result = {"jsonrpc": "2.0", "id": document["id"], "result": result}
        return Response(
            200, {"content-type": "application/json"}, json.dumps(result).encode()
        )


class Browser:
    def __init__(self) -> None:
        self.commands: list[Any] = []

    async def post_form(self, command) -> None:
        self.commands.append(command)


def connection(path: Path) -> tuple[OAuthClient, ChannelTransport, StateStore]:
    document = DiscoveryDocument.model_validate(
        {
            "protocol_version": "0.1",
            "organization": {"name": "Company", "domain": "company.example"},
            "auth": {"issuer": "https://company.example"},
            "apis": [
                {
                    "type": "openapi",
                    "url": "https://api.company.example/openapi.json",
                    "resource": "https://api.company.example",
                    "description": "API",
                },
                {
                    "type": "mcp",
                    "url": "https://mcp.company.example/endpoint",
                    "description": "Tools",
                },
            ],
            "web": {
                "browser_session_endpoint": "https://company.example/browser-session"
            },
            "extensions": {
                "operations": {
                    "version": "1",
                    "endpoint": "https://api.company.example/operations",
                    "resource": "https://api.company.example/operations",
                }
            },
        }
    )
    metadata = OAuthMetadata(
        issuer="https://company.example",
        token_endpoint="https://company.example/token",
        revocation_endpoint="https://company.example/revoke",
        poppy_domains=("company.example",),
        dpop_signing_alg_values_supported=("ES256",),
    )
    store, transport = StateStore(path), ChannelTransport()
    security = SecurityStore(store, encryption_key=Fernet.generate_key())
    oauth = OAuthClient(
        discovery=VerifiedDiscovery(
            document, metadata, "https://company.example/.well-known/poppy.json"
        ),
        scope=session().scope,
        connection="connection",
        identity=SigningKey.generate(),
        security=security,
        transport=transport,
    )
    version, record = oauth.record()
    record.update({"session": "session", "signed_in": False})
    for name in (
        "DPoP:",
        "DPoP:https://api.company.example",
        "DPoP:https://api.company.example/operations",
        "Bearer:https://mcp.company.example/endpoint",
    ):
        record["tokens"][name] = {
            "access": "private-token-" + name,
            "expires": time.time() + 3600,
            "scope": "",
        }
    security.put(oauth.scope, oauth.name, record, expected=version)
    return oauth, transport, store


def schema() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "servers": [{"url": "https://api.company.example/v1"}],
        "paths": {
            "/orders/{order}": {
                "parameters": [{"name": "order", "in": "path", "required": True}],
                "get": {
                    "operationId": "getOrder",
                    "parameters": [{"name": "fields", "in": "query"}],
                },
            },
            "/orders": {
                "post": {
                    "operationId": "createOrder",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                }
            },
        },
    }


class GatedRefresh(ChannelTransport):
    def __init__(self, oauth: OAuthClient) -> None:
        super().__init__()
        self.oauth = oauth
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def request(self, method: str, url: str, **kwargs: Any) -> Response:
        if url.endswith("/token"):
            self.entered.set()
            await self.release.wait()
        response = await super().request(method, url, **kwargs)
        if method == "GET":
            version, record = self.oauth.record()
            for token in record["tokens"].values():
                token["expires"] = 0
            self.oauth.security.put(
                self.oauth.scope, self.oauth.name, record, expected=version
            )
        return response
