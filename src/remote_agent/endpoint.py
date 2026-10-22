"""Authenticated reference HTTP ingress for accepted events and explicit controls."""

from __future__ import annotations

import hmac
import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import BaseRequestHandler
from typing import TYPE_CHECKING, Any, cast

from remote_agent.controller import AgentError, ControlError
from remote_agent.network import IngressDeadline
from remote_agent.storage import StoreError, canonical
from remote_agent.transport import InputVerificationError, TransportError
from remote_agent.types import JsonObject

if TYPE_CHECKING:
    from remote_agent.controller import HybridAgent


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseRequestHandler],
        max_connections: int,
        read_timeout: float,
    ) -> None:
        self.slots = threading.BoundedSemaphore(max_connections)
        self.read_timeout = read_timeout
        self.local = threading.local()
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        guard = None
        try:
            guard = IngressDeadline(request, self.read_timeout)
            self.local.guard = guard
            super().process_request_thread(request, client_address)
        except (OSError, RuntimeError):
            pass
        finally:
            if guard is not None:
                guard.close()
            self.shutdown_request(request)
            self.slots.release()

    def end_ingress(self) -> None:
        self.local.guard.close()


class Endpoint:
    def __init__(
        self,
        agent: HybridAgent,
        tokens: dict[str, JsonObject],
        *,
        notify: Callable[[JsonObject], JsonObject] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        max_body_bytes: int = 65_536,
        max_connections: int = 16,
        read_timeout: float = 5,
    ) -> None:
        if (
            host != "127.0.0.1"
            or type(max_body_bytes) is not int
            or max_body_bytes < 1
            or type(max_connections) is not int
            or max_connections < 1
            or read_timeout <= 0
        ):
            raise ValueError(
                "Reference HTTP endpoint requires loopback and positive limits; use a TLS proxy for remote access"
            )
        if not tokens or any(
            not isinstance(token, str)
            or len(token) < 32
            or not token.isascii()
            or not isinstance(grant, dict)
            or set(grant) != {"actor", "events"}
            or not isinstance(grant["actor"], str)
            or type(grant["events"]) is not bool
            for token, grant in tokens.items()
        ):
            raise ValueError("Explicit bearer tokens and endpoint grants are required")
        tokens = {token: dict(grant) for token, grant in tokens.items()}

        class Handler(BaseHTTPRequestHandler):
            server_version = "RemoteAgentReference/1"

            @property
            def http_server(self) -> Server:
                return cast(Server, self.server)

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(read_timeout)

            def log_message(self, format: str, *args: Any) -> None:
                pass

            def send_json(self, status: int, value: JsonObject) -> None:
                encoded = canonical(value)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(encoded)
                self.close_connection = True

            def do_POST(self) -> None:
                try:
                    headers = self.headers.get_all("Authorization", [])
                    if len(headers) != 1 or not headers[0].startswith("Bearer "):
                        return self.send_json(401, {"error": "Authentication required"})
                    presented = headers[0][7:]
                    grant = next(
                        (
                            value
                            for token, value in tokens.items()
                            if hmac.compare_digest(token.encode(), presented.encode())
                        ),
                        None,
                    )
                    if grant is None:
                        return self.send_json(401, {"error": "Authentication failed"})
                    if self.path not in ("/events", "/controls"):
                        return self.send_json(404, {"error": "Unknown endpoint"})
                    if self.path == "/events" and not grant["events"]:
                        return self.send_json(
                            403, {"error": "Accepted-event delivery grant required"}
                        )
                    lengths = self.headers.get_all("Content-Length", [])
                    if (
                        len(lengths) != 1
                        or not lengths[0].isdigit()
                        or self.headers.get("Transfer-Encoding")
                        or self.headers.get_content_type() != "application/json"
                    ):
                        return self.send_json(
                            400,
                            {"error": "JSON with one explicit Content-Length required"},
                        )
                    length = int(lengths[0])
                    if length > max_body_bytes:
                        return self.send_json(
                            413, {"error": "Request capacity exceeded"}
                        )
                    encoded = self.rfile.read(length)
                    if len(encoded) != length:
                        return self.send_json(400, {"error": "Incomplete request body"})

                    def unique_pairs(pairs: list[tuple[str, Any]]) -> JsonObject:
                        result = {}
                        for key, value in pairs:
                            if key in result:
                                raise ValueError("Duplicate JSON field")
                            result[key] = value
                        return result

                    data = json.loads(encoded, object_pairs_hook=unique_pairs)
                    try:
                        canonical(data)
                    except StoreError as error:
                        raise ValueError("Invalid canonical JSON") from error
                    if not isinstance(data, dict):
                        return self.send_json(400, {"error": "Expected JSON object"})
                    self.http_server.end_ingress()
                    if self.path == "/events":
                        if notify is None:
                            return self.send_json(
                                503, {"error": "Notification consumer unavailable"}
                            )
                        result = notify(data)
                    else:
                        result = agent.control(grant["actor"], data)
                    return self.send_json(
                        202 if result["status"] == "held" else 200, result
                    )
                except ControlError as error:
                    self.send_json(error.status, {"error": str(error)})
                except (ValueError, UnicodeError, RecursionError):
                    self.send_json(400, {"error": "Invalid JSON request"})
                except InputVerificationError as error:
                    self.send_json(error.status, {"error": str(error)})
                except AgentError as error:
                    self.send_json(409, {"error": str(error)})
                except (StoreError, TransportError):
                    self.send_json(
                        503,
                        {
                            "error": "Authority or persistence unavailable; delivery is not acknowledged"
                        },
                    )
                except TimeoutError:
                    self.send_json(408, {"error": "Request timeout"})
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = Server((host, port), Handler, max_connections, read_timeout)
        self.url = f"http://{host}:{self.server.server_address[1]}"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        )
        self.thread.start()
        self.closed = False

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(2)
