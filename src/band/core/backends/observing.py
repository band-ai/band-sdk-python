"""The per-turn tools proxy: delivery receipts, and the turn itself."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from band.core.contracts.delivery import DeliveryReceipt, receipt_from_tool_outcome
from band.core.protocols import AgentToolsProtocol, RunContext
from band.core.wrapping import ToolsWrapper
from band.runtime.tools import BAND_SEND_MESSAGE, ToolCallOutcome, is_room_posting_tool


@dataclass
class ObservingTools(ToolsWrapper):
    """Proxy that records a ``DeliveryReceipt`` on successful room posts.

    Both routes count: a model-driven room-posting tool call, and an adapter
    posting to the room itself (`send_message`) — a Copilot `ask_user` question,
    say. Adapters read the receipt through :func:`delivered` rather than
    tracking their own "already replied" flag.

    Minted once per turn and handed to the adapter as its ``tools``, so it also
    carries the turn's ``RunContext`` — the sink and cancellation token that
    ``on_message``'s signature has no room for (:func:`turn_context`).
    """

    _inner: AgentToolsProtocol
    turn: RunContext | None = None
    receipt: DeliveryReceipt | None = field(default=None, init=False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def participants(self) -> list[Any]:
        return self._inner.participants

    def record(self, receipt: DeliveryReceipt | None) -> None:
        """Keep the turn's first delivery; a later post does not replace it."""
        if self.receipt is None and receipt is not None:
            self.receipt = receipt

    async def send_message(self, *args: Any, **kwargs: Any) -> Any:
        result = await self._inner.send_message(*args, **kwargs)
        # Only a returning call delivered; a raising one posted nothing.
        self.record(DeliveryReceipt(tool_name=BAND_SEND_MESSAGE))
        return result

    async def execute_tool_call_structured(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> ToolCallOutcome:
        outcome = await self._inner.execute_tool_call_structured(tool_name, arguments)
        self.record(receipt_from_tool_outcome(tool_name, outcome))
        return outcome

    async def execute_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        # Match AgentTools: soft-fails return outcome.value (error string for the
        # LLM); never raise on ok=False. Receipt minting only watches structured.
        if hasattr(self._inner, "execute_tool_call_structured"):
            outcome = await self.execute_tool_call_structured(tool_name, arguments)
            return outcome.value
        result = await self._inner.execute_tool_call(tool_name, arguments)
        if is_room_posting_tool(tool_name):
            self.record(
                receipt_from_tool_outcome(
                    tool_name, ToolCallOutcome(value=result, ok=True)
                )
            )
        return result


def observer_in(tools: AgentToolsProtocol) -> ObservingTools | None:
    """The turn's delivery observer within ``tools``' proxy chain, if any.

    A turn's tools may arrive wrapped several times over (dedup, adapter-local
    proxies), so the observer is found by walking the chain — never by testing
    the object the adapter was handed. Every wrapper on the way must be a
    :class:`ToolsWrapper`, or the walk stops short of the observer.
    """
    cursor: AgentToolsProtocol = tools
    while isinstance(cursor, ToolsWrapper):
        if isinstance(cursor, ObservingTools):
            return cursor
        cursor = cursor.inner
    return None


async def send_non_reply_message(
    tools: AgentToolsProtocol,
    content: str,
    mentions: list[str] | list[dict[str, str]] | None = None,
) -> Any:
    """Post informational text without treating it as the turn's reply.

    Status notifications are visible room posts, but do not replace a model's
    final answer. Preserve wrappers inside the delivery observer while
    bypassing only that observer's receipt minting.
    """
    observer = observer_in(tools)
    target = observer.inner if observer is not None else tools
    return await target.send_message(content, mentions=mentions)


def delivered(tools: AgentToolsProtocol) -> DeliveryReceipt | None:
    """This turn's first successful room post, if it made one.

    The one question every adapter asks instead of keeping its own "did I
    already reply" flag: the backend wrapped the turn's tools in an
    :class:`ObservingTools`, which has been watching every call — and holds
    whatever out-of-process evidence :func:`record_delivery` added. Because the
    proxy is per turn, a call orphaned by an earlier turn records against that
    turn's proxy and can never mark a later one as having replied.
    """
    observer = observer_in(tools)
    return observer.receipt if observer is not None else None


def turn_context(tools: AgentToolsProtocol) -> RunContext | None:
    """The turn these ``tools`` belong to, if the backend minted one.

    Reached exactly like :func:`delivered`, because it is the same object: the
    per-turn observer the backend wrapped the turn's tools in. An adapter needs
    the turn's sink and cancellation token from inside ``on_message``, which
    takes neither — and resolving them from ``tools`` keeps them tied to the
    turn's own tools rather than to ambient task state, so a nested task can
    still emit onto the right sink and a finished turn's sink is unreachable.
    """
    observer = observer_in(tools)
    return observer.turn if observer is not None else None


def record_delivery(tools: AgentToolsProtocol, receipt: DeliveryReceipt | None) -> None:
    """Add delivery evidence the observer could not witness itself.

    Some room posts happen outside this process — an ACP agent's tool call
    running in a remote band-mcp, seen only as a session-update chunk. Folding
    that evidence into the same receipt keeps :func:`delivered` the single
    answer, and keeps ``RunResult.delivery`` honest about such turns.
    """
    observer = observer_in(tools)
    if observer is not None:
        observer.record(receipt)
