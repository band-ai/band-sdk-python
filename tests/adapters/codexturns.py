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
from band.integrations.codex import TRANSPORT_CLOSED, CodexRequestMethod, RpcEvent


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
        await turn.task
    else:
        await asyncio.wait_for(asyncio.shield(turn.task), timeout=timeout_s)


# Ready work settles within a few loop turns; this bounds a wait for work
# that never would.
MAX_LOOP_POLLS = 200
#: The model FakeCodexClient's model/list offers.
LISTED_MODEL = "gpt-5.5"
#: The id of the first thread a FakeCodexClient starts.
FIRST_THREAD_ID = "thr-1"


class FakeCodexClient(RecordedRequests):
    """Minimal fake transport client for adapter tests.

    Like a real app-server, it emits a turn's scripted events only once that
    turn starts: each ``turn/start`` releases the events up to and including
    the next ``turn/completed`` or transport close, and a scripted event that
    names no turn is stamped with the started turn's id. Once released
    events run out, the turn keeps working until something ends it.
    """

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
        started_settings: dict[str, Any] | None = None,
        resumed_settings: dict[str, Any] | None = None,
        resumed_thread_id: str | None = None,
        recorded_cwd: str | None = None,
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
        # What thread/start and thread/resume report the thread runs with.
        self._started_settings = started_settings or {}
        self._resumed_settings = resumed_settings or self._started_settings
        self._resumed_thread_id = resumed_thread_id
        # The cwd a resumed thread reports when resume names none.
        self._recorded_cwd = recorded_cwd
        self._thread_counter = 0
        self._turn_counter = 0
        self._released: deque[RpcEvent] = deque()
        self._emitted = asyncio.Event()
        self.turn_started = asyncio.Event()

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
                return {"data": [{"id": LISTED_MODEL, "hidden": False}]}
            case CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET:
                if self._skill_roots_error is not None:
                    raise self._skill_roots_error
            case CodexRequestMethod.THREAD_RESUME:
                if self._resume_error is not None:
                    raise self._resume_error
                thread_id = self._resumed_thread_id or payload.get("threadId")
                cwd = payload.get("cwd") or self._recorded_cwd
                return {
                    "thread": {"id": thread_id},
                    **({"cwd": cwd} if cwd else {}),
                    **self._resumed_settings,
                }
            case CodexRequestMethod.THREAD_START:
                self._thread_counter += 1
                thread_id = f"thr-{self._thread_counter}"
                return {"thread": {"id": thread_id}, **self._started_settings}
            case CodexRequestMethod.TURN_START:
                if self._turn_start_error is not None:
                    err = self._turn_start_error
                    if self._turn_start_error_once:
                        self._turn_start_error = None
                    raise err
                self._turn_counter += 1
                self._release_turn_events(f"turn-{self._turn_counter}")
                self.turn_started.set()
                return {
                    "turn": {
                        "id": f"turn-{self._turn_counter}",
                        "status": "inProgress",
                        "items": [],
                        "error": None,
                    }
                }

        return {}

    def _release_turn_events(self, turn_id: str) -> None:
        while self._events:
            event = self._events.popleft()
            self._released.append(_attributed(event, turn_id))
            if event.method in {"turn/completed", TRANSPORT_CLOSED}:
                break
        self._emitted.set()

    def emit(self, *events: RpcEvent) -> None:
        """Send events now, outside any scripted turn."""
        self._released.extend(events)
        self._emitted.set()

    async def read_by_adapter(self) -> None:
        """Wait until the adapter's reader has taken every event sent so far.

        The reader routes an event in the same step that takes it, so this
        also means every taken event has been routed.
        """
        for _ in range(MAX_LOOP_POLLS):
            if not self._released:
                return
            await asyncio.sleep(0)
        raise AssertionError("The adapter never read the events sent to it")

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent:
        while not self._released:
            self._emitted.clear()
            await asyncio.wait_for(self._emitted.wait(), timeout=timeout_s)
        return self._released.popleft()

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


def _attributed(event: RpcEvent, turn_id: str) -> RpcEvent:
    """``event`` naming ``turn_id`` when it names no turn of its own."""
    params = event.params if isinstance(event.params, dict) else {}
    if "turnId" in params or "turn" in params or event.method == TRANSPORT_CLOSED:
        return event
    params = {**params, "turnId": turn_id}
    return RpcEvent(
        kind=event.kind,
        method=event.method,
        params=params,
        id=event.id,
        raw={**event.raw, "params": params},
    )


def patch_codex_client(adapter: CodexAdapter, client: FakeCodexClient) -> None:
    def _build(_workspace: str) -> FakeCodexClient:
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
    return agent_message_completed(text)


def agent_message_started(
    item_id: str = "m", *, phase: str | None = "final_answer"
) -> RpcEvent:
    return event_notification(
        "item/started",
        {
            "threadId": FIRST_THREAD_ID,
            "startedAtMs": 1,
            "item": {"type": "agentMessage", "id": item_id, "text": "", "phase": phase},
        },
    )


def agent_message_delta(text: str, item_id: str = "m") -> RpcEvent:
    return event_notification(
        "item/agentMessage/delta",
        {"threadId": FIRST_THREAD_ID, "itemId": item_id, "delta": text},
    )


def agent_message_completed(
    text: str, item_id: str = "m", *, phase: str | None = "final_answer"
) -> RpcEvent:
    return event_notification(
        "item/completed",
        {
            "threadId": FIRST_THREAD_ID,
            "completedAtMs": 2,
            "item": {
                "type": "agentMessage",
                "id": item_id,
                "text": text,
                "phase": phase,
            },
        },
    )


def tool_call_request(
    request_id: int, tool: str, arguments: dict[str, Any] | None = None
) -> RpcEvent:
    """The server request Codex sends to invoke one Band tool."""
    return event_request(
        request_id, "item/tool/call", {"tool": tool, "arguments": arguments or {}}
    )
