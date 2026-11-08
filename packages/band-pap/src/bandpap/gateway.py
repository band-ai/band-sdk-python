"""Authenticated PAP company conversations backed by durable Band handoffs."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route as HttpRoute

from bandpap.auth import Access, AuthError, JWTAuthenticator
from bandpap.discovery import (
    AuthDiscovery,
    DiscoveryDocument,
    HttpsUrl,
    OAuthMetadata,
    validate_https_url,
)
from bandpap.operations import OperationError, Operations
from bandpap.storage import Session, StateConflict, StateStore, canonical
from bandpap.wire import (
    ConfirmOperation,
    ConversationResponse,
    DirectConversation,
    Event,
    Identifier,
    MessageEvent,
    RecordedMessage,
    SendMessage,
    StateEvent,
    parse_event,
)

Action = Literal[
    "create",
    "send",
    "read",
    "handoff",
    "close",
    "dispatch",
    "publish",
    "roster",
    "account",
]


class GatewayError(ValueError):
    def __init__(self, code: str, status: int, **details: str) -> None:
        super().__init__(code)
        self.code, self.status, self.details = code, status, details


class RetryAuthorization(Exception):
    """An atomic observation changed before its authorized transaction began."""


class GatewayModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GatewayGrant(GatewayModel):
    """Host-resolved routing and already-filtered participant projection."""

    id: str = Field(min_length=1)
    room: str = Field(min_length=1)
    proxy: str = Field(min_length=1)
    recipients: tuple[str, ...] = Field(min_length=1)
    visible_participants: tuple[dict[str, str], ...]

    @model_validator(mode="after")
    def validate_projection(self) -> GatewayGrant:
        if self.proxy in self.recipients or len(set(self.recipients)) != len(
            self.recipients
        ):
            raise ValueError("Proxy and recipient identities must be distinct")
        seen: set[str] = set()
        for peer in self.visible_participants:
            if (
                set(peer) - {"id", "name", "handle"}
                or not peer.get("id")
                or peer["id"] in seen
            ):
                raise ValueError(
                    "Participant projection exceeds the authorized binding"
                )
            seen.add(peer["id"])
        return self

    def binding(self) -> tuple[str, str, str, tuple[str, ...]]:
        return self.id, self.room, self.proxy, self.recipients


class GatewayConversation(GatewayModel):
    id: Identifier
    owner: Session
    grant: GatewayGrant
    status: Literal["working", "idle", "queued", "closed"]
    responder: Literal["agent", "human"]
    parent: Identifier | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class GatewayDelivery(GatewayModel):
    """A private, idempotently accepted handoff; no reusable credential is included."""

    id: str
    conversation_id: Identifier
    owner: Session
    grant: GatewayGrant
    kind: Literal["message", "handoff", "close"]
    message: RecordedMessage | None = None
    parent: Identifier | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class GatewayBand(Protocol):
    async def authorize(
        self, access: Access, action: Action, conversation: GatewayConversation | None
    ) -> GatewayGrant:
        """Resolve current live-session authority, membership and grants, or raise.

        Persisted Access values contain only historical claims. The host must
        revalidate revocation and current scopes here, including for dispatch.
        """
        ...

    async def dispatch(self, work: GatewayDelivery) -> None:
        """Durably accept work.id once under work.grant.proxy's Band identity.

        Returning means a durable host queue accepted it, not that a remote
        message or business effect completed. The host must fence an uncertain
        Band REST acceptance; local retries cannot prove exactly-once delivery.
        """
        ...


class ApprovalEvidence(Protocol):
    async def evidence(
        self,
        request: Request,
        access: Access,
        operation_id: str,
        confirmation: ConfirmOperation,
    ) -> object:
        """Resolve trusted approval evidence; protocol/chat claims are insufficient."""
        ...


class GatewayConfig(GatewayModel):
    public_url: HttpsUrl
    name: str = Field(min_length=1)
    auth: AuthDiscovery
    oauth_metadata: OAuthMetadata
    page_size: int = Field(default=100, ge=1, le=500)
    max_body_bytes: int = Field(default=1_048_576, ge=1, le=10_485_760)
    max_wait: float = Field(default=30, ge=0, le=120, allow_inf_nan=False)
    stream_seconds: float = Field(default=15, gt=0, le=120, allow_inf_nan=False)
    poll_seconds: float = Field(default=0.05, gt=0, le=1, allow_inf_nan=False)
    body_timeout: float = Field(default=10, gt=0, le=120, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_identity(self) -> GatewayConfig:
        parts = urlsplit(self.public_url)
        if parts.path not in {"", "/"} or parts.query:
            raise ValueError("Gateway public_url must be an HTTPS origin")
        if self.auth.issuer != self.oauth_metadata.issuer:
            raise ValueError("Discovery and OAuth issuer must exactly match")
        if (
            self.auth.direct is not None
            and self.oauth_metadata.authorization_endpoint is None
        ):
            raise ValueError("Direct sign-in requires an authorization endpoint")
        if (
            self.auth.device is not None
            and self.oauth_metadata.device_authorization_endpoint is None
        ):
            raise ValueError("Device sign-in requires a device authorization endpoint")
        if parts.hostname not in {
            domain.lower() for domain in self.oauth_metadata.poppy_domains
        }:
            raise ValueError("Issuer must vouch for the gateway domain")
        return self


SCHEMA = """
CREATE TABLE IF NOT EXISTS gateway_conversations (
 id TEXT PRIMARY KEY, authority TEXT NOT NULL, user TEXT NOT NULL, account TEXT,
 owner TEXT NOT NULL, grant_json TEXT NOT NULL, status TEXT NOT NULL,
 responder TEXT NOT NULL, parent TEXT, context TEXT NOT NULL DEFAULT '{}',
 version INTEGER NOT NULL DEFAULT 0,
 FOREIGN KEY(parent) REFERENCES gateway_conversations(id)
);
CREATE TABLE IF NOT EXISTS gateway_messages (
 scope TEXT NOT NULL, id TEXT NOT NULL, conversation TEXT NOT NULL,
 payload TEXT NOT NULL, response TEXT NOT NULL, status_code INTEGER NOT NULL,
 response_final INTEGER NOT NULL DEFAULT 1,
 PRIMARY KEY(scope,id), FOREIGN KEY(conversation) REFERENCES gateway_conversations(id)
);
CREATE TABLE IF NOT EXISTS gateway_events (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, conversation TEXT NOT NULL,
 id TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(conversation,id),
 FOREIGN KEY(conversation) REFERENCES gateway_conversations(id)
);
CREATE TABLE IF NOT EXISTS gateway_outbox (
 id TEXT PRIMARY KEY, conversation TEXT NOT NULL, payload TEXT NOT NULL,
 access_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending', lease TEXT,
 lease_until REAL, attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
 FOREIGN KEY(conversation) REFERENCES gateway_conversations(id)
);
"""


def identifier(prefix: str) -> str:
    return prefix + uuid4().hex


def authority(session: Session) -> str:
    return canonical(session.scope.model_dump(exclude={"user"}))


class PAPGateway:
    """A configured company boundary; callers never choose raw Band room IDs."""

    def __init__(
        self,
        config: GatewayConfig,
        *,
        store: StateStore,
        authenticator: JWTAuthenticator,
        band: GatewayBand,
        operations: Operations | None = None,
        approvals: ApprovalEvidence | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if authenticator.issuer != config.auth.issuer:
            raise ValueError("Authenticator must verify the configured issuer")
        validate_https_url(authenticator.audience)
        if (operations is None) != (approvals is None):
            raise ValueError("Operations require a trusted approval evidence interface")
        self.config, self.store, self.authenticator, self.band = (
            config,
            store,
            authenticator,
            band,
        )
        self.operations, self.approvals, self.clock = operations, approvals, clock
        self.origin = config.public_url.rstrip("/")
        self.endpoint = self.origin + "/poppy/conversations"
        self.operation_endpoint = self.origin + "/poppy/operations"
        self.store.db.executescript(SCHEMA)

        @asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncGenerator[None]:
            worker = asyncio.create_task(self.run_worker())
            try:
                yield
            finally:
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass

        self.app = Starlette(
            routes=[
                HttpRoute("/.well-known/poppy.json", self.discovery, methods=["GET"]),
                HttpRoute(
                    "/.well-known/oauth-authorization-server{suffix:path}",
                    self.metadata,
                    methods=["GET"],
                ),
                HttpRoute("/poppy/conversations", self.handle_create, methods=["POST"]),
                HttpRoute(
                    "/poppy/conversations/{conversation_id}/messages",
                    self.handle_send,
                    methods=["POST"],
                ),
                HttpRoute(
                    "/poppy/conversations/{conversation_id}/events",
                    self.handle_events,
                    methods=["GET"],
                ),
                HttpRoute(
                    "/poppy/conversations/{conversation_id}/handoff",
                    self.handle_control,
                    methods=["POST"],
                ),
                HttpRoute(
                    "/poppy/conversations/{conversation_id}/close",
                    self.handle_control,
                    methods=["POST"],
                ),
                HttpRoute(
                    "/poppy/operations/{operation_id}",
                    self.handle_operation,
                    methods=["GET"],
                ),
                HttpRoute(
                    "/poppy/operations/{operation_id}/{action}",
                    self.handle_operation,
                    methods=["POST"],
                ),
            ],
            lifespan=lifespan,
            exception_handlers={
                GatewayError: self.error,
                AuthError: self.error,
                ValidationError: self.error,
                OperationError: self.error,
                PermissionError: self.error,
                StateConflict: self.error,
                ValueError: self.error,
                Exception: self.error,
            },
        )

        async def sanitized_errors(request: Request, call_next: Any) -> Response:
            try:
                return await call_next(request)
            except Exception as error:  # noqa: BLE001 - host diagnostics must not escape through server exception logging
                return await self.error(request, error)

        self.app.add_middleware(BaseHTTPMiddleware, dispatch=sanitized_errors)

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        await self.app(scope, receive, send)

    async def error(self, request: Request, error: Exception) -> JSONResponse:
        headers: dict[str, str] = {"Cache-Control": "no-store"}
        if isinstance(error, GatewayError):
            if error.code == "extension_required":
                headers["WWW-Authenticate"] = (
                    'DPoP error="extension_required", extension="operations"'
                )
            return JSONResponse(
                {"error": error.code, **error.details}, error.status, headers=headers
            )
        if isinstance(error, AuthError):
            code = (
                error.code
                if error.code
                in {
                    "invalid_token",
                    "invalid_dpop_proof",
                    "use_dpop_nonce",
                    "sign_in_required",
                    "insufficient_scope",
                    "extension_required",
                }
                else "invalid_token"
            )
            status = (
                403
                if code
                in {"sign_in_required", "insufficient_scope", "extension_required"}
                else 401
            )
            details: dict[str, str] = {}
            if code in {"insufficient_scope", "extension_required"}:
                name = "scope" if code == "insufficient_scope" else "extension"
                value = getattr(error, name)
                if (
                    isinstance(value, str)
                    and value
                    and all(
                        (character == " " and name == "scope")
                        or (
                            33 <= ord(character) <= 126 and character not in {'"', "\\"}
                        )
                        for character in value
                    )
                ):
                    details[name] = value
                else:
                    code = "access_denied"
            headers["WWW-Authenticate"] = f'DPoP error="{code}"' + "".join(
                f', {name}="{value}"' for name, value in details.items()
            )
            if error.nonce is not None:
                headers["DPoP-Nonce"] = error.nonce
            return JSONResponse({"error": code, **details}, status, headers=headers)
        if isinstance(error, OperationError):
            value: dict[str, Any] = {"error": error.error}
            if error.operation is not None:
                value["operation"] = error.operation.wire()
            return JSONResponse(value, error.status_code, headers=headers)
        if isinstance(error, PermissionError):
            return JSONResponse({"error": "access_denied"}, 403, headers=headers)
        if isinstance(error, StateConflict):
            return JSONResponse({"error": "conflict"}, 409, headers=headers)
        if isinstance(error, (ValueError, ValidationError)):
            return JSONResponse({"error": "invalid_request"}, 400, headers=headers)
        return JSONResponse({"error": "server_error"}, 500, headers=headers)

    async def discovery(self, request: Request) -> JSONResponse:
        value: dict[str, Any] = {
            "protocol_version": "0.1",
            "organization": {
                "name": self.config.name,
                "domain": urlsplit(self.origin).hostname,
            },
            "auth": self.config.auth.model_dump(mode="json", exclude_none=True),
            "agent": {
                "protocols": [
                    {
                        "type": "poppy",
                        "endpoint": self.endpoint,
                        "resource": self.authenticator.audience,
                    }
                ]
            },
        }
        if self.operations is not None:
            value["extensions"] = {
                "operations": {
                    "version": "1",
                    "endpoint": self.operation_endpoint,
                    "resource": self.authenticator.audience,
                }
            }
        DiscoveryDocument.model_validate(value)
        return JSONResponse(value)

    async def metadata(self, request: Request) -> JSONResponse:
        suffix = urlsplit(self.config.auth.issuer).path.rstrip("/")
        if request.path_params["suffix"] != suffix:
            raise GatewayError("not_found", 404)
        return JSONResponse(
            self.config.oauth_metadata.model_dump(mode="json", exclude_none=True)
        )

    async def authenticate(self, request: Request) -> Access:
        if (
            len(request.headers.getlist("authorization")) != 1
            or len(request.headers.getlist("dpop")) != 1
        ):
            raise AuthError("invalid_token")
        # Public origin is configuration, never the untrusted Host/forwarded headers.
        raw_path = request.scope.get("raw_path", request.url.path.encode())
        try:
            url = self.origin + raw_path.decode("ascii")
        except (AttributeError, UnicodeError) as error:
            raise AuthError("invalid_dpop_proof") from error
        access = await self.authenticator.authenticate(
            authorization=request.headers["authorization"],
            proof=request.headers["dpop"],
            method=request.method,
            url=url,
        )
        if access.session.scope.direction != "inbound":
            raise AuthError("invalid_token")
        return access

    async def body(self, request: Request) -> dict[str, Any]:
        content = bytearray()
        try:
            async with asyncio.timeout(self.config.body_timeout):
                async for chunk in request.stream():
                    content.extend(chunk)
                    if len(content) > self.config.max_body_bytes:
                        raise GatewayError("invalid_request", 413)
        except TimeoutError as error:
            raise GatewayError("invalid_request", 408) from error
        value = json.loads(content)
        if not isinstance(value, dict):
            raise GatewayError("invalid_request", 400)
        return value

    def _row(self, access: Access, conversation_id: str) -> Any:
        row = self.store.db.execute(
            "SELECT * FROM gateway_conversations WHERE id=?", (conversation_id,)
        ).fetchone()
        session = access.session
        if (
            row is None
            or row["authority"] != authority(session)
            or row["user"] != session.scope.user
        ):
            raise GatewayError("conversation_not_found", 404)
        if row["account"] is not None and row["account"] != session.account:
            if row["user"] == session.scope.user and session.account is None:
                raise AuthError("sign_in_required")
            raise GatewayError("conversation_not_found", 404)
        return row

    def _conversation(
        self, access: Access, conversation_id: str
    ) -> GatewayConversation:
        row = self._row(access, conversation_id)
        return self._from_row(row)

    @staticmethod
    def _from_row(row: Any) -> GatewayConversation:
        return GatewayConversation(
            id=row["id"],
            owner=Session.model_validate_json(row["owner"]),
            grant=GatewayGrant.model_validate_json(row["grant_json"]),
            status=row["status"],
            responder=row["responder"],
            parent=row["parent"],
            context=json.loads(row["context"]),
        )

    async def _authorized(
        self, access: Access, action: Action, conversation_id: str
    ) -> tuple[GatewayConversation, GatewayGrant]:
        for _ in range(8):
            conversation = self._conversation(access, conversation_id)
            grant = await self.band.authorize(access, action, conversation)
            if grant.binding() != conversation.grant.binding():
                raise PermissionError("Conversation routing grant changed")
            if self._conversation(access, conversation_id) == conversation:
                return conversation, grant
        raise StateConflict("Conversation changed during authorization")

    @asynccontextmanager
    async def _guard(
        self, access: Access, action: Action, conversation_id: str
    ) -> AsyncGenerator[tuple[GatewayConversation, GatewayGrant]]:
        for _ in range(8):
            snapshot, grant = await self._authorized(access, action, conversation_id)
            with self.store.transaction():
                current = self._conversation(access, conversation_id)
                if current != snapshot:
                    continue
                yield current, grant
                return
        raise StateConflict("Conversation changed during authorization")

    def _append(self, conversation_id: str, value: dict[str, Any]) -> None:
        event = parse_event(value)
        previous = self.store.db.execute(
            "SELECT payload FROM gateway_events WHERE conversation=? AND id=?",
            (conversation_id, event.id),
        ).fetchone()
        payload = canonical(value)
        if previous:
            if previous["payload"] != payload:
                raise StateConflict("Event ID content changed")
            return
        self.store.db.execute(
            "INSERT INTO gateway_events(conversation,id,payload) VALUES(?,?,?)",
            (conversation_id, event.id, payload),
        )

    def _event(self, type_: str, **fields: Any) -> dict[str, Any]:
        return {
            "id": identifier("evt_"),
            "type": type_,
            "created_at": datetime.fromtimestamp(self.clock(), UTC).isoformat(),
            **fields,
        }

    def _state(self, conversation_id: str, status: str, responder: str) -> None:
        row = self.store.db.execute(
            "SELECT status,responder FROM gateway_conversations WHERE id=?",
            (conversation_id,),
        ).fetchone()
        if (row["status"], row["responder"]) != (status, responder):
            self.store.db.execute(
                "UPDATE gateway_conversations SET status=?,responder=?,version=version+1 WHERE id=?",
                (status, responder, conversation_id),
            )
            self._append(
                conversation_id,
                self._event("state", status=status, responder=responder),
            )

    def _outbox(
        self,
        access: Access,
        conversation: GatewayConversation,
        kind: Literal["message", "handoff", "close"],
        message: RecordedMessage | None = None,
    ) -> None:
        work = GatewayDelivery(
            id=identifier("work_"),
            conversation_id=conversation.id,
            owner=access.session,
            grant=conversation.grant,
            kind=kind,
            message=message,
            parent=conversation.parent,
            context=conversation.context,
        )
        self.store.db.execute(
            "INSERT INTO gateway_outbox(id,conversation,payload,access_json) VALUES(?,?,?,?)",
            (
                work.id,
                conversation.id,
                work.model_dump_json(exclude_unset=True),
                access.model_dump_json(),
            ),
        )

    def _open(self, access: Access, conversation_id: str) -> GatewayConversation:
        conversation = self._conversation(access, conversation_id)
        if conversation.status == "closed":
            raise GatewayError("conversation_closed", 409)
        child = self.store.db.execute(
            "SELECT id FROM gateway_conversations WHERE parent=? AND status!='closed'",
            (conversation_id,),
        ).fetchone()
        if child:
            raise GatewayError(
                "direct_conversation_open", 409, conversation_id=child["id"]
            )
        return conversation

    async def accept(
        self,
        access: Access,
        request: SendMessage,
        *,
        conversation_id: str | None = None,
        wait_response: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        for _ in range(8):
            try:
                return await self._accept_once(
                    access,
                    request,
                    conversation_id=conversation_id,
                    wait_response=wait_response,
                )
            except RetryAuthorization:
                continue
        raise StateConflict("Conversation changed during authorization")

    async def _accept_once(
        self,
        access: Access,
        request: SendMessage,
        *,
        conversation_id: str | None = None,
        wait_response: bool = False,
    ) -> tuple[int, dict[str, Any]]:
        parent = (
            request.parent_conversation_id
            if isinstance(request, DirectConversation)
            else None
        )
        authorized: dict[str, GatewayConversation] = {}
        original: GatewayConversation | None = None
        previous = self.store.db.execute(
            "SELECT conversation FROM gateway_messages WHERE scope=? AND id=?",
            (access.session.scope.key(), request.message.id),
        ).fetchone()
        if previous:
            original, _ = await self._authorized(
                access, "send", previous["conversation"]
            )
            authorized[original.id] = original
        if original is not None and conversation_id is None:
            grant = original.grant
        elif conversation_id is not None:
            conversation, grant = await self._authorized(
                access, "send", conversation_id
            )
            authorized[conversation.id] = conversation
        elif parent is not None:
            conversation, grant = await self._authorized(access, "create", parent)
            authorized[conversation.id] = conversation
            if conversation.parent is not None:
                raise GatewayError("conversation_not_found", 404)
        else:
            grant = await self.band.authorize(access, "create", None)
        payload = canonical(request.wire())
        with self.store.transaction():
            for original in authorized.values():
                if self._conversation(access, original.id) != original:
                    raise RetryAuthorization
            previous = self.store.db.execute(
                "SELECT * FROM gateway_messages WHERE scope=? AND id=?",
                (access.session.scope.key(), request.message.id),
            ).fetchone()
            if previous:
                if previous["conversation"] not in authorized:
                    raise RetryAuthorization
                if previous["payload"] != payload:
                    raise GatewayError("message_id_conflict", 409)
                original = self._conversation(access, previous["conversation"])
                if original.grant.binding() != grant.binding():
                    raise PermissionError(
                        "Retry does not have the original routing grant"
                    )
                return previous["status_code"], json.loads(previous["response"])
            if conversation_id is None:
                if parent is not None:
                    self._open(access, parent)
                conversation_id = identifier("cnv_")
                self.store.db.execute(
                    "INSERT INTO gateway_conversations(id,authority,user,account,owner,grant_json,status,responder,parent) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        conversation_id,
                        authority(access.session),
                        access.session.scope.user,
                        access.session.account,
                        access.session.model_dump_json(),
                        grant.model_dump_json(),
                        "working",
                        "agent",
                        parent,
                    ),
                )
                if parent:
                    self._append(
                        parent,
                        self._event("direct_opened", conversation_id=conversation_id),
                    )
                status = 201
            else:
                self._open(access, conversation_id)
                status = 202
            conversation = self._conversation(access, conversation_id)
            if conversation.parent is not None and request.message.sender != "human":
                raise GatewayError("invalid_request", 400)
            recorded = RecordedMessage.model_validate(
                request.message.wire() | {"role": "user"}
            )
            self._append(
                conversation_id, self._event("message", message=recorded.wire())
            )
            if request.message.context is not None:
                context = conversation.context | request.message.context.wire()
                self.store.db.execute(
                    "UPDATE gateway_conversations SET context=?,version=version+1 WHERE id=?",
                    (canonical(context), conversation_id),
                )
            if conversation.status not in {"queued", "closed"}:
                self._state(conversation_id, "working", conversation.responder)
            response = ConversationResponse(
                conversation_id=conversation_id,
                status=self._conversation(access, conversation_id).status,
                responder=conversation.responder,
            ).wire()
            self._outbox(
                access, self._conversation(access, conversation_id), "message", recorded
            )
            self.store.db.execute(
                "INSERT INTO gateway_messages VALUES(?,?,?,?,?,?,?)",
                (
                    access.session.scope.key(),
                    request.message.id,
                    conversation_id,
                    payload,
                    canonical(response),
                    status,
                    int(not wait_response),
                ),
            )
            return status, response

    async def control(
        self, access: Access, conversation_id: str, kind: Literal["handoff", "close"]
    ) -> dict[str, Any]:
        async with self._guard(access, kind, conversation_id) as (current, _grant):
            if kind == "handoff":
                self._open(access, conversation_id)
                if current.status != "queued" and current.responder != "human":
                    self._state(conversation_id, "queued", "agent")
                    self._outbox(access, current, kind)
            elif current.status != "closed":
                self._close(access, current)
                self._outbox(access, current, kind)
            current = self._conversation(access, conversation_id)
            return ConversationResponse(
                conversation_id=current.id,
                status=current.status,
                responder=current.responder,
            ).wire()

    def _close(
        self,
        access: Access,
        conversation: GatewayConversation,
        *,
        state_event: dict[str, Any] | None = None,
    ) -> None:
        for child in self.store.db.execute(
            "SELECT id FROM gateway_conversations WHERE parent=? AND status!='closed'",
            (conversation.id,),
        ).fetchall():
            child_row = self.store.db.execute(
                "SELECT * FROM gateway_conversations WHERE id=?", (child["id"],)
            ).fetchone()
            self._close(access, self._from_row(child_row))
        if state_event is not None:
            self.store.db.execute(
                "UPDATE gateway_conversations SET status='closed',responder=?,version=version+1 WHERE id=?",
                (state_event["responder"], conversation.id),
            )
            self._append(conversation.id, state_event)
        else:
            self._state(conversation.id, "closed", conversation.responder)
        if conversation.parent:
            self._append(
                conversation.parent,
                self._event("direct_closed", conversation_id=conversation.id),
            )

    def _page(
        self,
        access: Access,
        conversation_id: str,
        cursor: str | None,
        grant: GatewayGrant,
    ) -> dict[str, Any]:
        conversation = self._conversation(access, conversation_id)
        sequence = 0
        if cursor is not None:
            row = self.store.db.execute(
                "SELECT sequence FROM gateway_events WHERE conversation=? AND id=?",
                (conversation_id, cursor),
            ).fetchone()
            if row is None:
                raise GatewayError("invalid_cursor", 400)
            sequence = row["sequence"]
        rows = self.store.db.execute(
            "SELECT payload FROM gateway_events WHERE conversation=? AND sequence>? ORDER BY sequence LIMIT ?",
            (conversation_id, sequence, self.config.page_size + 1),
        ).fetchall()
        events = [json.loads(row["payload"]) for row in rows[: self.config.page_size]]
        for event in events:
            if event["type"] == "band_participants":
                event["participants"] = list(grant.visible_participants)
        return {
            "conversation_id": conversation_id,
            "events": events,
            "cursor": events[-1]["id"] if events else cursor,
            "has_more": len(rows) > self.config.page_size,
            "status": conversation.status,
            "responder": conversation.responder,
            "participants": list(grant.visible_participants),
        }

    async def read(
        self,
        access: Access,
        conversation_id: str,
        *,
        cursor: str | None = None,
        wait: int = 0,
    ) -> dict[str, Any]:
        if type(wait) is not int or wait < 0:
            raise GatewayError("invalid_request", 400)
        deadline = time.monotonic() + min(wait, self.config.max_wait)
        while True:
            async with self._guard(access, "read", conversation_id) as (
                _conversation,
                grant,
            ):
                page = self._page(access, conversation_id, cursor, grant)
            if page["events"] or time.monotonic() >= deadline:
                return page
            await asyncio.sleep(
                min(self.config.poll_seconds, max(0, deadline - time.monotonic()))
            )

    async def bind_account(self, access: Access, conversation_id: str) -> None:
        if access.session.account is None:
            raise AuthError("sign_in_required")
        async with self._guard(access, "account", conversation_id):
            self._row(access, conversation_id)
            self.store.db.execute(
                "UPDATE gateway_conversations SET account=?,version=version+1 WHERE id=?",
                (access.session.account, conversation_id),
            )
            self.store.db.execute(
                "UPDATE gateway_outbox SET access_json=? WHERE conversation=? AND state!='delivered'",
                (access.model_dump_json(), conversation_id),
            )

    async def resume(self, access: Access, conversation_id: str) -> None:
        """Host explicitly resumes paused handoffs with fresh live authority."""
        async with self._guard(access, "dispatch", conversation_id):
            self.store.db.execute(
                "UPDATE gateway_outbox SET state='pending',lease=NULL,lease_until=NULL,available_at=0,access_json=? WHERE conversation=? AND state='blocked'",
                (access.model_dump_json(), conversation_id),
            )

    async def publish(
        self,
        access: Access,
        conversation_id: str,
        event: Event | dict[str, Any],
        *,
        source_id: str,
        source_type: Literal["User", "Agent"],
        recipients: tuple[str, ...],
    ) -> None:
        if source_type not in {"User", "Agent"}:
            raise PermissionError("Unsupported Band author provenance")
        parsed = parse_event(event.wire() if isinstance(event, Event) else event)
        value = parsed.wire()
        if isinstance(parsed, MessageEvent):
            message = parsed.message
            if message.role != "company":
                raise ValueError("Company projections require company provenance")
            if message.sender != ("human" if source_type == "User" else "agent"):
                raise ValueError(
                    "Message authorship disagrees with authenticated Band source"
                )
        async with self._guard(access, "publish", conversation_id) as (
            conversation,
            grant,
        ):
            if source_id not in grant.recipients or grant.proxy not in recipients:
                raise PermissionError("Band reply is outside its addressed binding")
            previous = self.store.db.execute(
                "SELECT payload FROM gateway_events WHERE conversation=? AND id=?",
                (conversation_id, parsed.id),
            ).fetchone()
            if previous is not None:
                if previous["payload"] != canonical(value):
                    raise StateConflict("Event ID content changed")
                return
            if conversation.status == "closed":
                raise GatewayError("conversation_closed", 409)
            if isinstance(parsed, StateEvent) and parsed.status == "closed":
                self._close(access, conversation, state_event=value)
                return
            self._append(conversation_id, value)
            if isinstance(parsed, StateEvent):
                self.store.db.execute(
                    "UPDATE gateway_conversations SET status=?,responder=?,version=version+1 WHERE id=?",
                    (parsed.status, parsed.responder, conversation_id),
                )
            elif isinstance(parsed, MessageEvent):
                self._state(conversation_id, "idle", parsed.message.sender)

    async def publish_roster(self, access: Access, conversation_id: str) -> None:
        async with self._guard(access, "roster", conversation_id) as (
            _conversation,
            _grant,
        ):
            self._row(access, conversation_id)
            self._append(
                conversation_id,
                self._event("band_participants"),
            )

    async def flush(self, access: Access | None = None, *, limit: int = 100) -> int:
        """Replay token-free handoffs after authoritative host reauthorization."""
        count = 0
        rows = self.store.db.execute(
            "SELECT id FROM gateway_outbox WHERE (state='pending' OR (state='leased' AND lease_until<=?)) AND available_at<=? ORDER BY rowid LIMIT ?",
            (self.clock(), self.clock(), limit),
        ).fetchall()
        for candidate in rows:
            with self.store.transaction():
                row = self.store.db.execute(
                    "SELECT * FROM gateway_outbox WHERE id=?", (candidate["id"],)
                ).fetchone()
                if row["state"] == "delivered" or (
                    row["state"] == "leased" and row["lease_until"] > self.clock()
                ):
                    continue
                earliest = self.store.db.execute(
                    "SELECT id FROM gateway_outbox WHERE conversation=? AND state NOT IN ('delivered','blocked') ORDER BY rowid LIMIT 1",
                    (row["conversation"],),
                ).fetchone()
                if earliest["id"] != row["id"]:
                    continue
                original = Access.model_validate_json(row["access_json"])
                if (
                    access is not None
                    and access.session.scope != original.session.scope
                ):
                    continue
                caller = access or original
                work = GatewayDelivery.model_validate_json(row["payload"])
                lease = uuid4().hex
                self.store.db.execute(
                    "UPDATE gateway_outbox SET state='leased',lease=?,lease_until=? WHERE id=?",
                    (lease, self.clock() + 30, work.id),
                )
            try:
                _, grant = await self._authorized(
                    caller, "dispatch", work.conversation_id
                )
                if grant.binding() != work.grant.binding():
                    raise PermissionError("Pending work grant changed")
                await self.band.dispatch(work)
            except Exception as error:  # noqa: BLE001 - a failed host handoff stays durable and must not block other owners
                with self.store.transaction():
                    state = (
                        "blocked"
                        if isinstance(error, (PermissionError, AuthError, GatewayError))
                        else "pending"
                    )
                    retry_at = self.clock() + min(
                        30, 0.1 * 2 ** min(row["attempts"], 8)
                    )
                    self.store.db.execute(
                        "UPDATE gateway_outbox SET state=?,lease=NULL,lease_until=NULL,attempts=attempts+1,available_at=? WHERE id=? AND lease=?",
                        (state, retry_at, work.id, lease),
                    )
                continue
            with self.store.transaction():
                self.store.db.execute(
                    "UPDATE gateway_outbox SET state='delivered',lease=NULL,lease_until=NULL WHERE id=? AND lease=?",
                    (work.id, lease),
                )
            count += 1
        return count

    async def run_worker(self) -> None:
        """Owned by the ASGI lifespan; durable work survives process shutdown."""
        while True:
            try:
                await self.flush()
            except sqlite3.OperationalError:
                # Lock contention must not permanently stop the owned worker.
                pass
            await asyncio.sleep(self.config.poll_seconds)

    def _query(self, request: Request) -> tuple[str | None, int]:
        cursor = (
            request.headers.get("last-event-id")
            if "last-event-id" in request.headers
            else request.query_params.get("cursor")
        )
        raw = request.query_params.get("wait", "0")
        if not raw.isascii() or not raw.isdecimal():
            raise GatewayError("invalid_request", 400)
        # A server may cap very large waits without constructing unbounded ints.
        wait = int(raw) if len(raw) < 10 else int(self.config.max_wait)
        return cursor, wait

    async def handle_create(self, request: Request) -> JSONResponse:
        access = await self.authenticate(request)
        cursor, wait = self._query(request)
        value = await self.body(request)
        message = (
            DirectConversation.model_validate(value)
            if "parent_conversation_id" in value
            else SendMessage.model_validate(value)
        )
        status, response = await self.accept(
            access, message, wait_response="wait" in request.query_params
        )
        response = await self._post_response(
            access, message.message.id, response, cursor=cursor, wait=wait
        )
        return JSONResponse(response, status, headers={"Cache-Control": "no-store"})

    async def handle_send(self, request: Request) -> JSONResponse:
        access = await self.authenticate(request)
        cursor, wait = self._query(request)
        message = SendMessage.model_validate(await self.body(request))
        status, response = await self.accept(
            access,
            message,
            conversation_id=request.path_params["conversation_id"],
            wait_response="wait" in request.query_params,
        )
        response = await self._post_response(
            access, message.message.id, response, cursor=cursor, wait=wait
        )
        return JSONResponse(response, status, headers={"Cache-Control": "no-store"})

    async def _post_response(
        self,
        access: Access,
        message_id: str,
        accepted: dict[str, Any],
        *,
        cursor: str | None,
        wait: int,
    ) -> dict[str, Any]:
        row = self.store.db.execute(
            "SELECT response,response_final FROM gateway_messages WHERE scope=? AND id=?",
            (access.session.scope.key(), message_id),
        ).fetchone()
        if row["response_final"]:
            return json.loads(row["response"])
        response = await self.read(
            access, accepted["conversation_id"], cursor=cursor, wait=wait
        )
        # Roster projections are current views, never cached retry responses.
        response.pop("participants")
        for event in response["events"]:
            if event["type"] == "band_participants":
                event.pop("participants", None)
        with self.store.transaction():
            self._row(access, accepted["conversation_id"])
            self.store.db.execute(
                "UPDATE gateway_messages SET response=?,response_final=1 WHERE scope=? AND id=? AND response_final=0",
                (canonical(response), access.session.scope.key(), message_id),
            )
            return json.loads(
                self.store.db.execute(
                    "SELECT response FROM gateway_messages WHERE scope=? AND id=?",
                    (access.session.scope.key(), message_id),
                ).fetchone()["response"]
            )

    async def handle_events(self, request: Request) -> Response:
        access = await self.authenticate(request)
        cursor, wait = self._query(request)
        conversation_id = request.path_params["conversation_id"]
        if "text/event-stream" not in request.headers.get("accept", ""):
            return JSONResponse(
                await self.read(access, conversation_id, cursor=cursor, wait=wait),
                headers={"Cache-Control": "no-store"},
            )
        page = await self.read(access, conversation_id, cursor=cursor)
        token = request.headers["authorization"].partition(" ")[2]
        expiry = jwt.decode(token, options={"verify_signature": False})["exp"]

        async def stream() -> AsyncGenerator[str]:
            current = page
            deadline = time.monotonic() + self.config.stream_seconds
            while True:
                for event in current["events"]:
                    yield f"id: {event['id']}\ndata: {canonical(event)}\n\n"
                cursor_ = current["cursor"]
                if time.monotonic() >= deadline or self.clock() >= expiry:
                    return
                try:
                    # The proof was consumed once; the host rechecks live authority.
                    current = await self.read(access, conversation_id, cursor=cursor_)
                except Exception:  # noqa: BLE001 - end a started stream without exposing host diagnostics
                    return
                if not current["events"]:
                    await asyncio.sleep(self.config.poll_seconds)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    async def handle_control(self, request: Request) -> JSONResponse:
        access = await self.authenticate(request)
        if await self.body(request) != {}:
            raise GatewayError("invalid_request", 400)
        kind = "handoff" if request.url.path.endswith("/handoff") else "close"
        result = await self.control(
            access, request.path_params["conversation_id"], kind
        )
        return JSONResponse(
            result,
            202 if kind == "handoff" else 200,
            headers={"Cache-Control": "no-store"},
        )

    async def handle_operation(self, request: Request) -> JSONResponse:
        access = await self.authenticate(request)
        if (
            self.operations is None
            or self.approvals is None
            or access.extensions.get("operations") != "1"
        ):
            raise GatewayError("extension_required", 403, extension="operations")
        operation_id = request.path_params["operation_id"]
        action = request.path_params.get("action", "read")
        if request.method == "GET":
            result = await self.operations.read(access.session, operation_id)
        elif action == "confirm":
            confirmation = ConfirmOperation.model_validate(await self.body(request))
            evidence = await self.approvals.evidence(
                request, access, operation_id, confirmation
            )
            result = await self.operations.confirm(
                access.session, operation_id, confirmation, evidence=evidence
            )
        elif action == "cancel":
            if await self.body(request) != {}:
                raise GatewayError("invalid_request", 400)
            result = await self.operations.cancel(access.session, operation_id)
        else:
            raise GatewayError("not_found", 404)
        return JSONResponse(result.wire(), headers={"Cache-Control": "no-store"})
