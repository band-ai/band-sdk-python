"""Codex test helpers -- a scripted app-server and its event builders --
shared by the codex adapter tests and the turn-outcome conformance probe.

Once a manual approval releases the room, ``on_message``/``on_event`` returns
early and the turn keeps running in ``adapter._turn_tasks``, out of reach of
the caller that awaited the handler -- so a test must await it separately to
observe the turn's outcome.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from band.adapters.codex import CodexAdapter, CodexAdapterConfig
from band.integrations.codex import CodexRequestMethod, RpcEvent


class RecordedRequests:
    """Views over the ``(method, params)`` requests a fake Codex client saw."""

    requests: list[tuple[str, dict[str, Any]]]

    @property
    def request_methods(self) -> list[str]:
        return [method for method, _ in self.requests]

    def params_of(self, method: CodexRequestMethod) -> list[dict[str, Any]]:
        return [params for sent, params in self.requests if sent == method]


async def await_released_turn(
    adapter: CodexAdapter, room_id: str, *, timeout_s: float | None = None
) -> None:
    """Await ``room_id``'s detached turn, if one is running.

    A no-op when no turn is tracked (the room never released one). Pass
    ``timeout_s`` to bound the wait; omit it to await unconditionally.
    """
    turn = adapter._turn_tasks.get(room_id)
    if turn is None:
        return
    if timeout_s is None:
        await turn
    else:
        await asyncio.wait_for(turn, timeout=timeout_s)


class FakeCodexClient(RecordedRequests):
    """Minimal fake transport client for adapter tests."""

    def __init__(
        self,
        *,
        events: list[RpcEvent] | None = None,
        resume_error: Exception | None = None,
        turn_start_error: Exception | None = None,
        turn_start_error_once: bool = True,
        model_list_result: dict[str, Any] | None = None,
        model_list_error: Exception | None = None,
        skill_roots_error: Exception | None = None,
    ) -> None:
        self.connected = False
        self.initialized = False
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.responses: list[tuple[int | str, dict[str, Any]]] = []
        self.response_errors: list[tuple[int | str, int, str]] = []
        self.closed = False
        self._events = deque(events or [])
        self._resume_error = resume_error
        self._turn_start_error = turn_start_error
        self._turn_start_error_once = turn_start_error_once
        self._model_list_result = model_list_result
        self._model_list_error = model_list_error
        self._skill_roots_error = skill_roots_error
        self._thread_counter = 0
        self._turn_counter = 0

    async def connect(self) -> None:
        self.connected = True

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]:
        self.initialized = True
        return {"userAgent": f"{client_name}/{client_version}"}

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]:
        payload = params or {}
        self.requests.append((method, dict(payload)))

        match method:
            case CodexRequestMethod.MODEL_LIST:
                if self._model_list_error is not None:
                    raise self._model_list_error
                if self._model_list_result is not None:
                    return self._model_list_result
                return {"data": [{"id": "gpt-5.5", "hidden": False}]}
            case CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET:
                if self._skill_roots_error is not None:
                    raise self._skill_roots_error
            case CodexRequestMethod.THREAD_RESUME:
                if self._resume_error is not None:
                    raise self._resume_error
                return {"thread": {"id": payload.get("threadId", "thr-resumed")}}
            case CodexRequestMethod.THREAD_START:
                self._thread_counter += 1
                return {"thread": {"id": f"thr-{self._thread_counter}"}}
            case CodexRequestMethod.TURN_START:
                if self._turn_start_error is not None:
                    err = self._turn_start_error
                    if self._turn_start_error_once:
                        self._turn_start_error = None
                    raise err
                self._turn_counter += 1
                return {
                    "turn": {
                        "id": f"turn-{self._turn_counter}",
                        "status": "inProgress",
                        "items": [],
                        "error": None,
                    }
                }

        return {}

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
        if not self._events:
            raise TimeoutError
        return self._events.popleft()

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        self.responses.append((request_id, result))

    async def respond_error(
        self,
        request_id: int | str,
        *,
        code: int,
        message: str,
        data: Any | None = None,
    ) -> None:
        self.response_errors.append((request_id, code, message))

    async def close(self) -> None:
        self.closed = True


def patch_codex_client(adapter: CodexAdapter, client: FakeCodexClient) -> None:
    def _build(_config: CodexAdapterConfig) -> FakeCodexClient:
        return client

    adapter._build_client = _build  # type: ignore[method-assign]


def make_codex_adapter(
    client: FakeCodexClient,
    config: CodexAdapterConfig | None = None,
    **kwargs: Any,
) -> CodexAdapter:
    adapter = CodexAdapter(config=config or CodexAdapterConfig(), **kwargs)
    patch_codex_client(adapter, client)
    return adapter


def event_notification(method: str, params: dict[str, Any]) -> RpcEvent:
    return RpcEvent(
        kind="notification",
        method=method,
        params=params,
        id=None,
        raw={"method": method, "params": params},
    )


def event_request(request_id: int, method: str, params: dict[str, Any]) -> RpcEvent:
    return RpcEvent(
        kind="request",
        method=method,
        params=params,
        id=request_id,
        raw={"id": request_id, "method": method, "params": params},
    )


def turn_completed(turn_id: str = "turn-1", *, status: str = "completed") -> RpcEvent:
    """The notification that ends a scripted turn."""
    return event_notification(
        "turn/completed",
        {"turn": {"id": turn_id, "status": status, "items": [], "error": None}},
    )


def final_text(text: str) -> RpcEvent:
    return event_notification("item/agentMessage/delta", {"itemId": "m", "delta": text})


def tool_call_request(
    request_id: int, tool: str, arguments: dict[str, Any] | None = None
) -> RpcEvent:
    """The server request Codex sends to invoke one Band tool."""
    return event_request(
        request_id, "item/tool/call", {"tool": tool, "arguments": arguments or {}}
    )
