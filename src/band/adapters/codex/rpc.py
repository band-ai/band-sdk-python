"""The Codex app-server JSON-RPC client contract and event fields."""

from __future__ import annotations

from typing import Any, Protocol

from band.integrations.codex import RpcEvent
from band.integrations.codex.types import CodexItemType


class CodexClientProtocol(Protocol):
    async def connect(self) -> None: ...

    async def initialize(
        self,
        *,
        client_name: str,
        client_title: str,
        client_version: str,
        experimental_api: bool = False,
        opt_out_notification_methods: list[str] | None = None,
    ) -> dict[str, Any]: ...

    async def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        retry_on_overload: bool = True,
    ) -> dict[str, Any]: ...

    async def recv_event(self, timeout_s: float | None = None) -> RpcEvent: ...

    async def respond(self, request_id: int | str, result: dict[str, Any]) -> None: ...

    async def respond_error(
        self, request_id: int | str, *, code: int, message: str, data: Any | None = None
    ) -> None: ...

    async def close(self) -> None: ...


_METHOD_NOT_FOUND = -32601


async def refuse_unhandled_request(
    client: CodexClientProtocol, *, request_id: int | str, method: str
) -> None:
    await client.respond_error(
        request_id,
        code=_METHOD_NOT_FOUND,
        message=f"Unhandled server request: {method}",
    )


def event_params(event: RpcEvent) -> dict[str, Any]:
    return event.params if isinstance(event.params, dict) else {}


def event_turn_id(event: RpcEvent) -> str | None:
    """The native turn ``event`` belongs to: its ``turnId``, or the ``turn.id``
    that ``turn/started`` and ``turn/completed`` carry instead."""
    params = event_params(event)
    turn_id = params.get("turnId")
    if turn_id is None and isinstance(turn := params.get("turn"), dict):
        turn_id = turn.get("id")
    return str(turn_id) if turn_id else None


def loss_reason(cause: RpcEvent | Exception) -> str:
    """Why a process's events stopped: the transport's stated reason, if any."""
    if isinstance(cause, Exception):
        return repr(cause)
    params = event_params(cause)
    return str(params.get("reason") or cause.method)


def event_thread_id(event: RpcEvent) -> str | None:
    params = event_params(event)
    thread_id = params.get("threadId")
    return str(thread_id) if thread_id else None


def delegated_threads(event: RpcEvent) -> set[str]:
    """Sub-agent threads an item says the turn spawned or addressed."""
    item = event_params(event).get("item")
    if not isinstance(item, dict):
        return set()
    match item.get("type"):
        case CodexItemType.COLLAB_AGENT_TOOL_CALL:
            threads = item.get("receiverThreadIds") or ()
        case CodexItemType.SUB_AGENT_ACTIVITY:
            threads = (item.get("agentThreadId"),)
        case _:
            return set()
    return {str(thread) for thread in threads if thread}


def result_entry_id(result: Any, key: str) -> str:
    """The ``id`` of ``result[key]``, or "" when Codex returned none."""
    entry = result.get(key) if isinstance(result, dict) else None
    return str(entry.get("id") or "") if isinstance(entry, dict) else ""
