"""Shared JSON-RPC protocol logic for Codex transport clients."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

logger = logging.getLogger(__name__)

JsonRpcId = int | str


class CodexJsonRpcError(RuntimeError):
    """JSON-RPC error returned by codex app-server."""

    def __init__(
        self,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"Codex JSON-RPC error {code}: {message}")


@dataclass(frozen=True)
class OverloadRetryPolicy:
    """Retry policy for retryable server overload errors (-32001)."""

    max_attempts: int = 3
    base_delay_s: float = 0.25
    max_delay_s: float = 2.0
    jitter_s: float = 0.1

    def backoff_seconds(self, attempt_number: int) -> float:
        """Compute exponential backoff delay for retry attempt N (1-based)."""
        base = min(self.base_delay_s * (2 ** (attempt_number - 1)), self.max_delay_s)
        if self.jitter_s <= 0:
            return base
        return max(0.0, base + random.uniform(-self.jitter_s, self.jitter_s))


@dataclass(frozen=True)
class RpcEvent:
    """Non-response JSON-RPC message from server."""

    kind: Literal["notification", "request"]
    method: str
    params: dict[str, Any] | list[Any] | None
    id: JsonRpcId | None
    raw: dict[str, Any]


class BaseJsonRpcClient:
    """Abstract bidirectional JSON-RPC client with shared protocol logic.

    Subclasses implement transport-specific ``connect``, ``close``, and
    ``_send_json`` methods.  Read loops in subclasses should call
    ``_dispatch_rpc_message`` for each incoming text frame/line.
    """

    def __init__(
        self,
        *,
        retry_policy: OverloadRetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.retry_policy = retry_policy or OverloadRetryPolicy()
        self._sleep = sleep

        self._pending: dict[JsonRpcId, asyncio.Future[dict[str, Any]]] = {}
        self._events: asyncio.Queue[RpcEvent] = asyncio.Queue(maxsize=10_000)
        self._request_id = 0
        self._connected = False
        self._transport_label = "payload"  # overridden by subclasses for diagnostics

    # ------------------------------------------------------------------
    # Transport methods
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open the underlying transport."""
        raise NotImplementedError

    async def close(self) -> None:
        """Close the underlying transport."""
        raise NotImplementedError

    async def _send_json(self, payload: dict[str, Any]) -> None:
        """Serialize *payload* as JSON and send over the transport."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Protocol methods (shared)
    # ------------------------------------------------------------------

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]:
        """Perform initialize request followed by initialized notification."""
        capabilities: dict[str, Any] = {"experimentalApi": experimental_api}
        if opt_out_notification_methods:
            capabilities["optOutNotificationMethods"] = opt_out_notification_methods

        result = await self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": client_name,
                    "title": client_title,
                    "version": client_version,
                },
                "capabilities": capabilities,
            },
            retry_on_overload=False,
        )
        await self.notify("initialized", {})
        return result

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        """Send request and await result, with overload retry when enabled."""
        attempt = 0
        while True:
            attempt += 1
            try:
                return await self._request_once(method, params or {})
            except CodexJsonRpcError as err:
                if not retry_on_overload or not self._is_retryable_overload(err):
                    raise
                if attempt >= self.retry_policy.max_attempts:
                    raise
                delay_s = self.retry_policy.backoff_seconds(attempt)
                logger.warning(
                    "Codex overloaded for method=%s; retrying in %.2fs (attempt %s/%s)",
                    method,
                    delay_s,
                    attempt + 1,
                    self.retry_policy.max_attempts,
                )
                await self._sleep(delay_s)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send JSON-RPC notification."""
        await self._send_json(
            {
                "method": method,
                "params": params or {},
            }
        )

    async def respond(self, request_id: JsonRpcId, result: dict[str, Any]) -> None:
        """Send JSON-RPC response to a server-initiated request."""
        await self._send_json({"id": request_id, "result": result})

    async def respond_error(
        self,
        request_id: JsonRpcId,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        """Send JSON-RPC error response to a server-initiated request."""
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._send_json({"id": request_id, "error": error})

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
        """Receive next notification/request emitted by server."""
        if timeout_s is None:
            return await self._events.get()
        return await asyncio.wait_for(self._events.get(), timeout=timeout_s)

    # ------------------------------------------------------------------
    # Internal helpers (shared)
    # ------------------------------------------------------------------

    async def _request_once(
        self, method: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        request_id = self._next_request_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        try:
            await self._send_json(
                {
                    "method": method,
                    "id": request_id,
                    "params": params,
                }
            )
            response = await future
        finally:
            self._pending.pop(request_id, None)

        if "error" in response:
            error = response.get("error") or {}
            raise CodexJsonRpcError(
                code=int(error.get("code", -32000)),
                message=str(error.get("message", "Unknown JSON-RPC error")),
                data=error.get("data"),
            )
        return response.get("result") or {}

    def _fail_pending(self, reason: str) -> None:
        """Fail all outstanding request futures and enqueue a disconnect event."""
        error = RuntimeError(reason)
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
        try:
            self._events.put_nowait(
                RpcEvent(
                    kind="notification",
                    method="transport/closed",
                    params={"reason": reason},
                    id=None,
                    raw={"method": "transport/closed", "params": {"reason": reason}},
                )
            )
        except asyncio.QueueFull:
            logger.warning("Event queue full; disconnect event dropped")

    async def _dispatch_rpc_message(self, text: str) -> None:
        """Parse a raw JSON-RPC text frame and route it."""
        text = text.strip()
        if not text:
            return

        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Skipping non-JSON %s: %s", self._transport_label, text)
            return

        if not isinstance(message, dict):
            logger.warning("Skipping non-object JSON-RPC payload: %s", message)
            return

        msg_id = message.get("id")
        method = message.get("method")

        if method and msg_id is not None:
            try:
                self._events.put_nowait(
                    RpcEvent(
                        kind="request",
                        method=str(method),
                        params=message.get("params"),
                        id=msg_id,
                        raw=message,
                    )
                )
            except asyncio.QueueFull:
                logger.warning("Event queue full; dropping server request %s", method)
            return

        if method:
            try:
                self._events.put_nowait(
                    RpcEvent(
                        kind="notification",
                        method=str(method),
                        params=message.get("params"),
                        id=None,
                        raw=message,
                    )
                )
            except asyncio.QueueFull:
                logger.warning("Event queue full; dropping notification %s", method)
            return

        if msg_id is not None:
            future = self._pending.get(msg_id)
            if future is not None and not future.done():
                future.set_result(message)
            return

        logger.debug("Ignoring unknown JSON-RPC payload: %s", message)

    def _next_request_id(self) -> int:
        self._request_id += 1
        return self._request_id

    @staticmethod
    def _is_retryable_overload(err: CodexJsonRpcError) -> bool:
        return err.code == -32001 and "overload" in err.message.lower()
