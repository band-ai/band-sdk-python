"""Async adapter bridge for the OpenAI Codex Python SDK."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from .rpc_base import CodexJsonRpcError, RpcEvent

logger = logging.getLogger(__name__)

_EVENT_QUEUE_SIZE = 10_000


@dataclass(frozen=True)
class _PendingServerRequest:
    method: str
    response_queue: queue.Queue[dict[str, Any]]


class CodexSdkClient:
    """Async protocol bridge backed by ``openai-codex``'s low-level client.

    The public ``openai_codex.Codex`` facade intentionally hides raw protocol
    details that Band needs, especially ``dynamicTools`` and server-initiated
    tool calls.  The lower-level ``openai_codex.client.CodexClient`` still
    accepts raw request dictionaries and lets callers handle server requests, so
    this class adapts that client to the async protocol expected by
    ``CodexAdapter``.
    """

    def __init__(
        self,
        *,
        command: tuple[str, ...] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        client_name: str = "band_codex_adapter",
        client_title: str = "Band Codex Adapter",
        client_version: str = "0.1.0",
        experimental_api: bool = True,
    ) -> None:
        self._command = command
        self._cwd = cwd
        self._env = env
        self._client_name = client_name
        self._client_title = client_title
        self._client_version = client_version
        self._experimental_api = experimental_api

        self._client: Any | None = None
        self._connected = False
        self._closed = False
        self._events: queue.Queue[RpcEvent | BaseException] = queue.Queue(
            maxsize=_EVENT_QUEUE_SIZE
        )
        self._pending_lock = threading.Lock()
        self._pending_requests: dict[str, _PendingServerRequest] = {}
        self._next_server_request_id = 0
        self._turn_tasks: set[asyncio.Task[None]] = set()

    async def connect(self) -> None:
        """Start the bundled Codex runtime."""
        if self._connected:
            return

        try:
            client_module = import_module("openai_codex.client")
        except ImportError as exc:
            raise RuntimeError(
                "The 'openai-codex' package is required for CodexAdapter. "
                "Install band-sdk[codex] or add openai-codex to your environment."
            ) from exc
        codex_client_cls = getattr(client_module, "CodexClient")
        codex_config_cls = getattr(client_module, "CodexConfig")

        config = codex_config_cls(
            launch_args_override=self._command,
            cwd=self._cwd,
            env=self._env,
            client_name=self._client_name,
            client_title=self._client_title,
            client_version=self._client_version,
            experimental_api=self._experimental_api,
        )
        self._client = codex_client_cls(
            config=config,
            approval_handler=self._handle_server_request,
        )
        self._closed = False
        await asyncio.to_thread(self._client.start)
        self._connected = True

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]:
        """Initialize the Codex runtime session."""
        del client_name, client_title, client_version, experimental_api
        if opt_out_notification_methods:
            logger.debug(
                "openai-codex does not expose opt-out notifications; ignoring %s",
                opt_out_notification_methods,
            )
        client = self._require_client()
        try:
            result = await asyncio.to_thread(client.initialize)
        except Exception as exc:
            raise self._compatible_rpc_error(exc) from exc
        return self._model_to_dict(result)

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        """Send one of the raw app-server requests used by CodexAdapter."""
        client = self._require_client()
        payload = params or {}

        def call() -> Any:
            if method == "model/list":
                include_hidden = bool(payload.get("includeHidden", False))
                return client.model_list(include_hidden=include_hidden)

            if method == "thread/start":
                return client.thread_start(payload)

            if method == "thread/resume":
                thread_id = str(payload.get("threadId") or "")
                if not thread_id:
                    raise CodexJsonRpcError(
                        code=-32602,
                        message="thread/resume requires threadId",
                    )
                return client.thread_resume(thread_id, payload)

            if method == "turn/start":
                thread_id = str(payload.get("threadId") or "")
                turn_input = payload.get("input")
                if not thread_id or not isinstance(turn_input, list):
                    raise CodexJsonRpcError(
                        code=-32602,
                        message="turn/start requires threadId and input list",
                    )
                return client.turn_start(thread_id, turn_input, params=payload)

            if method == "turn/interrupt":
                thread_id = str(payload.get("threadId") or "")
                turn_id = str(payload.get("turnId") or "")
                if not thread_id or not turn_id:
                    raise CodexJsonRpcError(
                        code=-32602,
                        message="turn/interrupt requires threadId and turnId",
                    )
                return client.turn_interrupt(thread_id, turn_id)

            raise CodexJsonRpcError(
                code=-32601,
                message=f"Unsupported Codex SDK request method: {method}",
            )

        try:
            if retry_on_overload:
                retry_module = import_module("openai_codex.retry")
                retry = getattr(retry_module, "retry_on_overload")
                result = await asyncio.to_thread(retry, call)
            else:
                result = await asyncio.to_thread(call)
        except Exception as exc:
            raise self._compatible_rpc_error(exc) from exc

        data = self._model_to_dict(result)
        if method == "turn/start":
            turn = data.get("turn") if isinstance(data, dict) else None
            turn_id = (
                str((turn or {}).get("id") or "") if isinstance(turn, dict) else ""
            )
            if turn_id:
                self._start_turn_pump(turn_id)
        return data

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
        """Receive the next Codex notification or server request."""
        try:
            item = await asyncio.to_thread(self._get_event, timeout_s)
        except queue.Empty as exc:
            raise asyncio.TimeoutError from exc
        if isinstance(item, BaseException):
            raise item
        return item

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        """Respond to a server request routed through ``recv_event``."""
        pending = self._pop_pending_request(str(request_id))
        if pending is None:
            logger.debug("No pending Codex server request for id=%s", request_id)
            return
        pending.response_queue.put(result)

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        """Best-effort error response for the SDK's handler-only request hook.

        The published OpenAI SDK request hook returns a JSON-RPC result, not a
        JSON-RPC error envelope.  Returning a failed dynamic-tool style result is
        the closest compatible response and, more importantly, prevents the SDK
        reader thread from blocking indefinitely.
        """
        del code, data
        pending = self._pop_pending_request(str(request_id))
        if pending is None:
            logger.debug("No pending Codex server request for id=%s", request_id)
            return
        pending.response_queue.put(
            {
                "contentItems": [{"type": "inputText", "text": message}],
                "success": False,
            }
        )

    async def close(self) -> None:
        """Close the Codex runtime and wake pending waiters."""
        self._closed = True
        for task in list(self._turn_tasks):
            task.cancel()
        if self._turn_tasks:
            await asyncio.gather(*self._turn_tasks, return_exceptions=True)
        self._turn_tasks.clear()

        self._fail_pending_server_requests()
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
        self._client = None
        self._connected = False
        self._enqueue_event(RuntimeError("Codex SDK client closed"))

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("Codex SDK client is not connected")
        return self._client

    def _handle_server_request(
        self,
        method: str,
        params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        request_id = self._allocate_server_request_id()
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending_requests[request_id] = _PendingServerRequest(
                method=method,
                response_queue=response_queue,
            )

        request_params = params or {}
        self._enqueue_event(
            RpcEvent(
                kind="request",
                method=method,
                params=request_params,
                id=request_id,
                raw={"id": request_id, "method": method, "params": request_params},
            )
        )

        response = response_queue.get()
        return response

    def _allocate_server_request_id(self) -> str:
        with self._pending_lock:
            self._next_server_request_id += 1
            return f"codex-sdk-request-{self._next_server_request_id}"

    def _pop_pending_request(self, request_id: str) -> _PendingServerRequest | None:
        with self._pending_lock:
            return self._pending_requests.pop(request_id, None)

    def _fail_pending_server_requests(self) -> None:
        with self._pending_lock:
            pending = list(self._pending_requests.values())
            self._pending_requests.clear()
        for item in pending:
            item.response_queue.put(
                self._failed_tool_response(
                    "Codex SDK client closed before the request completed."
                )
            )

    def _fail_pending_server_request(self, request_id: str, message: str) -> None:
        pending = self._pop_pending_request(request_id)
        if pending is None:
            return
        pending.response_queue.put(self._failed_tool_response(message))

    @staticmethod
    def _failed_tool_response(message: str) -> dict[str, Any]:
        return {
            "contentItems": [
                {
                    "type": "inputText",
                    "text": message,
                }
            ],
            "success": False,
        }

    def _start_turn_pump(self, turn_id: str) -> None:
        task = asyncio.create_task(self._pump_turn_notifications(turn_id))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    async def _pump_turn_notifications(self, turn_id: str) -> None:
        client = self._require_client()
        try:
            while not self._closed:
                notification = await asyncio.to_thread(
                    client.next_turn_notification,
                    turn_id,
                )
                event = self._notification_to_event(notification)
                self._enqueue_event(event)
                if event.method == "turn/completed":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not self._closed:
                logger.warning(
                    "Codex SDK notification pump failed for turn %s",
                    turn_id,
                    exc_info=True,
                )
                self._enqueue_event(
                    RpcEvent(
                        kind="notification",
                        method="transport/closed",
                        params={"reason": str(exc)},
                        id=None,
                        raw={
                            "method": "transport/closed",
                            "params": {"reason": str(exc)},
                        },
                    )
                )
        finally:
            try:
                await asyncio.to_thread(client.unregister_turn_notifications, turn_id)
            except Exception:
                logger.debug(
                    "Failed to unregister Codex SDK turn notifications for %s",
                    turn_id,
                    exc_info=True,
                )

    def _notification_to_event(self, notification: Any) -> RpcEvent:
        params = self._notification_params(notification)
        method = str(getattr(notification, "method", ""))
        return RpcEvent(
            kind="notification",
            method=method,
            params=params,
            id=None,
            raw={"method": method, "params": params},
        )

    def _notification_params(self, notification: Any) -> dict[str, Any]:
        payload = getattr(notification, "payload", None)
        if payload is None:
            return {}
        if hasattr(payload, "params"):
            raw_params = getattr(payload, "params")
            if isinstance(raw_params, dict):
                return raw_params
        return self._model_to_dict(payload)

    def _get_event(self, timeout_s: float | None) -> RpcEvent | BaseException:
        if timeout_s is None:
            return self._events.get()
        return self._events.get(timeout=timeout_s)

    def _enqueue_event(self, item: RpcEvent | BaseException) -> None:
        try:
            self._events.put_nowait(item)
            return
        except queue.Full:
            logger.warning("Codex SDK event queue is full; dropping oldest event")

        try:
            dropped = self._events.get_nowait()
        except queue.Empty:
            dropped = None

        if (
            isinstance(dropped, RpcEvent)
            and dropped.kind == "request"
            and dropped.id is not None
        ):
            self._fail_pending_server_request(
                str(dropped.id),
                "Codex SDK event queue dropped this server request before Band could handle it.",
            )

        try:
            self._events.put_nowait(item)
        except queue.Full:
            logger.warning(
                "Codex SDK event queue is still full; dropping incoming event"
            )
            if (
                isinstance(item, RpcEvent)
                and item.kind == "request"
                and item.id is not None
            ):
                self._fail_pending_server_request(
                    str(item.id),
                    "Codex SDK event queue was full before Band could handle this server request.",
                )

    @staticmethod
    def _model_to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            dumped = value.model_dump(
                by_alias=True,
                exclude_none=True,
                mode="json",
            )
            if isinstance(dumped, dict):
                return dumped
        return {}

    @staticmethod
    def _compatible_rpc_error(exc: Exception) -> Exception:
        if isinstance(exc, CodexJsonRpcError):
            return exc
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None)
        if isinstance(code, int) and isinstance(message, str):
            return CodexJsonRpcError(
                code=code,
                message=message,
                data=getattr(exc, "data", None),
            )
        return exc
