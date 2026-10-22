"""Authenticated HTTP services and a deadline-bounded remote room client."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import ssl
import sys
import threading
from collections.abc import Callable
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import BaseRequestHandler
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import SplitResult, urlsplit

from remote_agent.commands import CommandError, call
from remote_agent.controller import AgentError, ControlError
from remote_agent.diagnostics import agent_summary
from remote_agent.environment import minimal_environment
from remote_agent.protocol import check_depth
from remote_agent.storage import StoreError, canonical
from remote_agent.transport import (
    InboxCursorConflict,
    InputVerificationError,
    ReferenceRoom,
    TransportError,
)
from remote_agent.types import JsonObject, SourcePath

if TYPE_CHECKING:
    from remote_agent.authority import DurableRoom
if TYPE_CHECKING:
    from remote_agent.controller import HybridAgent
if TYPE_CHECKING:
    from remote_agent.interfaces import HTTPBackend


logger = logging.getLogger(__name__)
PERMISSIONS = {"room", "events", "controls", "observe"}


class HTTPError(Exception):
    def __init__(self, status: int, message: str, *, code: str | None = None) -> None:
        self.status, self.code = status, code
        super().__init__(message)


def strict_json(encoded: str | bytes) -> JsonObject:
    def unique(pairs: list[tuple[str, Any]]) -> JsonObject:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON field")
            result[key] = value
        return result

    value = json.loads(encoded, object_pairs_hook=unique)
    check_depth(value)
    canonical(value)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def validate_url(url: str) -> SplitResult:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise TransportError(
            "An explicit HTTP(S) room URL without credentials or query is required"
        )
    if parsed.scheme == "http":
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise TransportError("Remote room connections require TLS")
    return parsed


class IngressDeadline:
    def __init__(self, connection: socket.socket, seconds: float) -> None:
        # Keep an independent descriptor: TLS wrapping detaches the original socket.
        descriptor = os.dup(connection.fileno())
        try:
            self.socket: socket.socket | None = socket.socket(fileno=descriptor)
        except BaseException:
            os.close(descriptor)
            raise
        try:
            self.lock = threading.Lock()
            self.timer = threading.Timer(seconds, self.expire)
            self.timer.daemon = True
            self.timer.start()
        except BaseException:
            self.socket.close()
            self.socket = None
            raise

    def expire(self) -> None:
        with self.lock:
            if self.socket is not None:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close(self) -> None:
        self.timer.cancel()
        with self.lock:
            if self.socket is not None:
                self.socket.close()
                self.socket = None


class Server(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    # Rebind the configured port after a stopped service leaves TCP TIME_WAIT.
    # SO_REUSEPORT stays disabled: two live listeners must never share authority.
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseRequestHandler],
        *,
        context: ssl.SSLContext | None,
        max_connections: int,
        read_timeout: float,
    ) -> None:
        self.context, self.read_timeout = context, read_timeout
        self.slots = threading.BoundedSemaphore(max_connections)
        self.local, self.metrics_lock = threading.local(), threading.Lock()
        self.counters = {
            "requests": 0,
            "capacity_rejections": 0,
            "active_connections": 0,
            "responses": {},
        }
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            with self.metrics_lock:
                self.counters["capacity_rejections"] += 1
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        guard = None
        with self.metrics_lock:
            self.counters["active_connections"] += 1
        try:
            guard = IngressDeadline(request, self.read_timeout)
            self.local.guard = guard
            request.settimeout(self.read_timeout)
            if self.context is not None:
                request = self.context.wrap_socket(request, server_side=True)
            self.finish_request(request, client_address)
        except (OSError, ValueError, RuntimeError):
            pass
        finally:
            if guard is not None:
                guard.close()
            self.shutdown_request(request)
            with self.metrics_lock:
                self.counters["active_connections"] -= 1
            self.slots.release()

    def end_ingress(self) -> None:
        self.local.guard.close()

    def metrics(self) -> JsonObject:
        with self.metrics_lock:
            return json.loads(canonical(self.counters))


class HTTPService:
    def __init__(
        self,
        backend: HTTPBackend,
        tokens: dict[str, JsonObject],
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        tls_certificate: SourcePath | None = None,
        tls_key: SourcePath | None = None,
        max_body_bytes: int = 65_536,
        max_connections: int = 8,
        read_timeout: float = 5,
    ) -> None:
        if (
            host != "127.0.0.1"
            and not tls_certificate
            or bool(tls_certificate) != bool(tls_key)
            or type(max_body_bytes) is not int
            or not 1 <= max_body_bytes <= 1_048_576
            or type(max_connections) is not int
            or not 1 <= max_connections <= 128
            or not 0 < read_timeout <= 120
        ):
            raise ValueError(
                "Public binds require TLS and all HTTP limits must be positive and bounded"
            )
        if (
            not isinstance(tokens, dict)
            or not 1 <= len(tokens) <= 256
            or any(
                not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_~+/.=-]{32,1024}", token)
                or not isinstance(grant, dict)
                or set(grant) != {"actor", "permissions"}
                or not isinstance(grant["actor"], str)
                or not grant["actor"]
                or not isinstance(grant["permissions"], list)
                or any(
                    permission not in PERMISSIONS for permission in grant["permissions"]
                )
                for token, grant in tokens.items()
            )
        ):
            raise ValueError(
                "Explicit bearer identities and permission grants are required"
            )
        tokens = json.loads(canonical(tokens))
        context = None
        if tls_certificate:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(tls_certificate, tls_key)

        class Handler(BaseHTTPRequestHandler):
            server_version = "ContractService/1"
            sys_version = ""

            @property
            def http_server(self) -> Server:
                return cast(Server, self.server)

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def send_json(self, status: int, value: JsonObject) -> None:
                self.http_server.end_ingress()
                encoded = canonical(value)
                if len(encoded) > 4_194_304:
                    status, encoded = 503, b'{"error":"Response capacity exceeded"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                with self.http_server.metrics_lock:
                    counts = self.http_server.counters
                    counts["requests"] += 1
                    counts["responses"][str(status)] = (
                        counts["responses"].get(str(status), 0) + 1
                    )
                logger.info(
                    "%s", json.dumps({"event": "http_request", "status": status})
                )
                self.wfile.write(encoded)

            def send_error(
                self, code: int, message: str | None = None, explain: str | None = None
            ) -> None:
                self.send_json(code, {"error": "Invalid HTTP request"})

            def grant(self) -> JsonObject:
                headers = self.headers.get_all("Authorization", [])
                if len(headers) != 1 or not headers[0].startswith("Bearer "):
                    raise HTTPError(401, "Authentication required")
                presented = headers[0][7:].encode()
                grant = next(
                    (
                        grant
                        for token, grant in tokens.items()
                        if hmac.compare_digest(token.encode(), presented)
                    ),
                    None,
                )
                if grant is None:
                    raise HTTPError(401, "Authentication failed")
                return grant

            def handle_api(self) -> None:
                try:
                    if self.command == "GET" and self.path == "/health/live":
                        return self.send_json(200, {"live": True})
                    grant = self.grant()
                    if self.command == "GET" and self.path == "/health/ready":
                        self.http_server.end_ingress()
                        ready = bool(backend.ready())
                        return self.send_json(200 if ready else 503, {"ready": ready})
                    if self.command == "GET" and self.path == "/metrics":
                        if "observe" not in grant["permissions"]:
                            raise HTTPError(403, "Observer grant required")
                        self.http_server.end_ingress()
                        return self.send_json(
                            200,
                            {
                                "http": self.http_server.metrics(),
                                "backend": backend.metrics(),
                            },
                        )
                    data = None
                    if self.command == "POST":
                        lengths = self.headers.get_all("Content-Length", [])
                        if (
                            len(lengths) != 1
                            or not lengths[0].isdigit()
                            or self.headers.get_all("Transfer-Encoding")
                            or self.headers.get_content_type() != "application/json"
                        ):
                            raise HTTPError(
                                400, "JSON with one explicit Content-Length required"
                            )
                        length = int(lengths[0])
                        if length > max_body_bytes:
                            raise HTTPError(413, "Request capacity exceeded")
                        encoded = self.rfile.read(length)
                        if len(encoded) != length:
                            raise HTTPError(400, "Incomplete request body")
                        try:
                            data = strict_json(encoded)
                        except (ValueError, UnicodeError, RecursionError, StoreError):
                            raise HTTPError(400, "Invalid JSON object") from None
                    self.http_server.end_ingress()
                    status, result = backend.dispatch(
                        self.command, self.path, grant, data
                    )
                    return self.send_json(status, result)
                except HTTPError as error:
                    body = {"error": str(error)}
                    if error.code is not None:
                        body["code"] = error.code
                    self.send_json(error.status, body)
                except ControlError as error:
                    self.send_json(error.status, {"error": str(error)})
                except InputVerificationError as error:
                    self.send_json(error.status, {"error": str(error)})
                except (StoreError, TransportError):
                    self.send_json(
                        503,
                        {
                            "error": "Service unavailable; acceptance is not acknowledged"
                        },
                    )
                except AgentError:
                    self.send_json(409, {"error": "Agent cannot accept this request"})
                except TimeoutError:
                    self.send_json(408, {"error": "Request deadline exceeded"})
                except Exception:  # noqa: BLE001 -- HTTP responses must redact arbitrary backend exception details.
                    self.send_json(500, {"error": "Service operation failed"})

            def do_POST(self) -> None:
                self.handle_api()

            def do_GET(self) -> None:
                self.handle_api()

        self.server = Server(
            (host, port),
            Handler,
            context=context,
            max_connections=max_connections,
            read_timeout=read_timeout,
        )
        self.port = self.server.server_address[1]
        self.url = f"{'https' if context else 'http'}://{host}:{self.port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.closed = False
        self.thread.start()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)


class RoomAPI:
    def __init__(self, room: DurableRoom) -> None:
        self.room = room

    def ready(self) -> bool:
        return (
            not self.room.failed
            and not self.room.engine.closed
            and self.room.store.db is not None
            and self.room.engine.process.poll() is None
        )

    def metrics(self) -> JsonObject:
        return {
            "operations": self.room.store.get("room_operations"),
            "ready": self.ready(),
            "storage": self.room.store.metrics(),
        }

    def dispatch(
        self, method: str, path: str, grant: JsonObject, data: JsonObject | None
    ) -> tuple[int, JsonObject]:
        if (method, path) not in (
            ("GET", "/v1/identity"),
            ("GET", "/v1/definition"),
            ("POST", "/v1/view"),
            ("POST", "/v1/proposals"),
            ("POST", "/v1/inbox"),
        ):
            raise HTTPError(404, "Unknown endpoint")
        actor = grant["actor"]
        if (
            "room" not in grant["permissions"]
            or actor not in self.room.config["bindings"].values()
        ):
            raise HTTPError(403, "Bound room participant required")
        if not self.ready():
            raise HTTPError(503, "Room requires recovery")
        if method == "GET" and path == "/v1/identity":
            return 200, {
                "profile": "contract-room/http/1",
                "session": self.room.session,
                "identity": self.room.identity,
                "actor": actor,
                "idempotent": True,
                "inbox_profile": "contract-room/inbox/1",
            }
        if method == "GET" and path == "/v1/definition":
            # Use the pinned binding, never a role supplied by the HTTP caller.
            # The engine's immutable definition operation does not sample time,
            # expire duties, or mutate the durable room journal.
            with self.room.lock:
                if not self.ready():
                    raise HTTPError(503, "Room requires recovery")
                role = next(
                    role
                    for role, bound in self.room.config["bindings"].items()
                    if bound == actor
                )
                definition = self.room.engine.request(op="definition", role=role)
                return 200, {
                    "profile": "contract-room/definition/1",
                    "session": self.room.session,
                    "identity": self.room.identity,
                    "actor": actor,
                    "definition": definition,
                }
        if data is None:
            raise HTTPError(400, "Expected JSON object")
        if method == "POST" and path == "/v1/view":
            if data != {}:
                raise HTTPError(400, "View request body must be empty")
            projection = self.room.view(actor)
            del projection["view"]["definition"]
            return 200, projection
        if method == "POST" and path == "/v1/proposals":
            return 200, self.room.publish(data, actor)
        if method == "POST" and path == "/v1/inbox":
            if (
                set(data) != {"after", "limit"}
                or type(data["after"]) is not int
                or not 0 <= data["after"] <= 9_007_199_254_740_991
                or type(data["limit"]) is not int
                or not 1 <= data["limit"] <= 128
            ):
                raise HTTPError(400, "Invalid inbox cursor or page limit")
            with self.room.lock:
                head = self.room.store.journal_head()
                if data["after"] > head["sequence"]:
                    raise HTTPError(
                        409,
                        "Inbox cursor is ahead of the room journal",
                        code="cursor_ahead",
                    )
                cursor, events, size = data["after"], [], 0
                for frame in self.room.store.records(after=cursor, limit=data["limit"]):
                    result = (
                        frame["data"].get("result", {})
                        if frame["kind"] == "room_operation"
                        else {}
                    )
                    if (
                        result.get("status") == "accepted"
                        and actor in result["recipients"]
                    ):
                        # Acceptance time belongs to the committed authority request,
                        # not this read or the participant's clock. Keep the stored
                        # engine receipt byte-for-byte unchanged for retry/replay.
                        accepted_at = frame["data"].get("request", {}).get("now")
                        if (
                            type(accepted_at) is int
                            and 0 <= accepted_at <= 9_007_199_254_740_991
                        ):
                            result = dict(result, at=accepted_at)
                        encoded = canonical(result)
                        if size + len(encoded) > 2_097_152:
                            if not events:
                                raise HTTPError(
                                    503,
                                    "Accepted event exceeds inbox response capacity",
                                )
                            break
                        events.append(result)
                        size += len(encoded)
                    cursor = frame["sequence"]
                return 200, {"cursor": cursor, "events": events, "head": head}
        raise HTTPError(404, "Unknown endpoint")


class AgentAPI:
    def __init__(
        self,
        agent: HybridAgent,
        *,
        notify: Callable[[JsonObject], JsonObject] | None = None,
    ) -> None:
        self.agent, self.notify = agent, notify

    def ready(self) -> bool:
        return not self.agent.closed and self.agent.store.db is not None

    def metrics(self) -> JsonObject:
        store = self.agent.store
        return dict(agent_summary(store), ready=self.ready(), storage=store.metrics())

    def dispatch(
        self, method: str, path: str, grant: JsonObject, data: JsonObject | None
    ) -> tuple[int, JsonObject]:
        if method != "POST" or path not in ("/v1/events", "/v1/controls"):
            raise HTTPError(404, "Unknown endpoint")
        permission = "events" if path == "/v1/events" else "controls"
        if permission not in grant["permissions"]:
            raise HTTPError(403, "Endpoint grant required")
        if not self.ready():
            raise HTTPError(503, "Agent is stopping")
        if data is None:
            raise HTTPError(400, "Expected JSON object")
        if permission == "events":
            if self.notify is None:
                raise HTTPError(503, "Notification consumer unavailable")
            result = self.notify(data)
        else:
            result = self.agent.control(grant["actor"], data)
        return (202 if result["status"] == "held" else 200), result


class HTTPRoom(ReferenceRoom):
    def __init__(
        self,
        url: str,
        token: str,
        *,
        ca_file: SourcePath | None = None,
        timeout: float = 10,
        require_inbox: bool = False,
        authentication: str = "bearer",
    ) -> None:
        validate_url(url)
        if authentication not in ("bearer", "band-api-key"):
            raise TransportError("Unsupported room authentication scheme")
        if (
            not isinstance(token, str)
            or not re.fullmatch(r"[A-Za-z0-9_~+/.=-]{32,1024}", token)
            or not 0.001 <= timeout <= 120
        ):
            raise TransportError("Invalid room credential or timeout configuration")
        self.url, self.token, self.ca_file, self.timeout = (
            url.rstrip("/"),
            token,
            ca_file,
            timeout,
        )
        self.authentication = authentication
        info = self._request("GET", "/v1/identity")
        if (
            info.get("profile") != "contract-room/http/1"
            or info.get("idempotent") is not True
            or not isinstance(info.get("identity"), dict)
            or not isinstance(info.get("actor"), str)
            or not isinstance(info.get("session"), str)
        ):
            raise TransportError("Unsupported room authority capabilities")
        self._identity, self.actor, self.session = (
            info["identity"],
            info["actor"],
            info["session"],
        )
        self._definition: JsonObject | None = None
        self._definition_lock = threading.Lock()
        self.inbox_profile = info.get("inbox_profile")
        if require_inbox and self.inbox_profile != "contract-room/inbox/1":
            raise TransportError(
                "Room authority does not support the required durable inbox profile"
            )

    @property
    def identity(self) -> JsonObject:
        return self._identity

    def _request(
        self, method: str, path: str, data: JsonObject | None = None
    ) -> JsonObject:
        bootstrap = "import sys;sys.path.insert(0,sys.argv[1]);from remote_agent.http_worker import main;main()"
        try:
            result = call(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    "-c",
                    bootstrap,
                    str(Path(__file__).resolve().parent.parent),
                ],
                {
                    "url": self.url + path,
                    "method": method,
                    "token": self.token,
                    "authentication": self.authentication,
                    "ca_file": self.ca_file,
                    "timeout_ms": round(self.timeout * 1000),
                    "data": data,
                },
                timeout=self.timeout,
                max_bytes=4_194_304,
                env=minimal_environment(path="/usr/bin:/bin"),
            )
            if "error" in result:
                raise TransportError("Room connection failed")
            if result.get("status") != 200:
                body = result.get("body")
                if (
                    method == "POST"
                    and path == "/v1/inbox"
                    and result.get("status") == 409
                    and isinstance(body, dict)
                    and body.get("code") == "cursor_ahead"
                    and isinstance(body.get("error"), str)
                ):
                    raise InboxCursorConflict(
                        "Room inbox HTTP status 409; explicit host reconciliation required"
                    )
                raise TransportError("Room HTTP status " + str(result.get("status")))
            return result["body"]
        except (CommandError, OSError, ValueError, KeyError, StoreError) as error:
            raise TransportError(
                "Room request failed or exceeded its deadline"
            ) from error

    def _actor(self, actor: str) -> None:
        if actor != self.actor:
            raise TransportError(
                "Requested actor differs from the authenticated identity"
            )

    def view(self, actor: str, now: int | None = None) -> JsonObject:
        self._actor(actor)
        projection = self._request("POST", "/v1/view", {})
        # Legacy authorities already return the definition. Compact HTTP views
        # retain their preflight/checkpoint; only runner callers need hydration.
        if "definition" not in projection["view"]:
            with self._definition_lock:
                if self._definition is None:
                    response = self._request("GET", "/v1/definition")
                    if (
                        response.get("profile") != "contract-room/definition/1"
                        or response.get("session") != self.session
                        or response.get("actor") != self.actor
                        or response.get("identity") != self.identity
                        or not isinstance(response.get("definition"), dict)
                    ):
                        raise TransportError(
                            "Room definition differs from the authenticated session identity"
                        )
                    self._definition = response["definition"]
                projection["view"]["definition"] = deepcopy(self._definition)
        return projection

    def publish(
        self, message: JsonObject, actor: str, now: int | None = None
    ) -> JsonObject:
        self._actor(actor)
        return self._request("POST", "/v1/proposals", message)

    def inbox(self, after: int, *, limit: int = 100) -> JsonObject:
        if self.inbox_profile != "contract-room/inbox/1":
            raise TransportError(
                "Room authority does not support the required durable inbox profile"
            )
        return self._request("POST", "/v1/inbox", {"after": after, "limit": limit})
