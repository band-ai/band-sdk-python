"""Tool-loop ``AgentBackend`` parameterized by a ``ModelProvider``."""

from __future__ import annotations

import asyncio
import json
from itertools import count
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from band.core.backends.history import (
    SessionHistoryPolicy,
    ToolRoundItem,
)
from band.core.contracts import (
    BackendContext,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelSamplingOptions,
    ModelToolCall,
    RunResult,
    ThoughtEvent,
    ToolCallEvent,
    ToolStatus,
    ToolResultEvent,
)
from band.core.contracts.delivery import DeliveryReceipt, receipt_from_tool_outcome
from band.core.exceptions import MaxToolRoundsExceeded
from band.core.protocols import ModelProvider, RunContext
from band.core.run.context import ProviderModelContext
from band.core.types import PlatformMessage, TurnUsage
from band.runtime.tools import ToolCallOutcome, ToolDefinition

ExecuteFn = Callable[[RunContext, ModelToolCall], Awaitable[ToolCallOutcome]]

_CANCELLED_TOOL_CALL_MESSAGE = (
    "Tool call was not executed because the turn was cancelled."
)


@dataclass
class NativeToolLoopBackend:
    """Session + tool-loop orchestrator parameterized by a ``ModelProvider``."""

    provider: ModelProvider
    system: str = ""
    max_tool_rounds: int | None = 8
    on_max_rounds: Literal["thought", "raise"] = "thought"
    tool_definitions: list[ToolDefinition] = field(default_factory=list)
    sampling: ModelSamplingOptions | None = None
    raw_options: dict[str, Any] | None = None
    # Resolved once in ``__post_init__``, from the provider's default when not
    # supplied, and written back here so the field always reads true. The turn
    # uses ``_policy``, the same object under a non-optional type.
    history_policy: SessionHistoryPolicy | None = None
    execute_override: ExecuteFn | None = None
    _policy: SessionHistoryPolicy = field(init=False, repr=False)
    _sessions: dict[str, list[ModelMessage]] = field(default_factory=dict, init=False)
    _turn_usage: dict[str, TurnUsage] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        if self.max_tool_rounds is not None and (
            not isinstance(self.max_tool_rounds, int)
            or isinstance(self.max_tool_rounds, bool)
        ):
            raise TypeError("max_tool_rounds must be an int or None")
        if self.on_max_rounds not in {"thought", "raise"}:
            raise ValueError("on_max_rounds must be 'thought' or 'raise'")
        if self.history_policy is not None:
            self._policy = self.history_policy
            return
        # A duck-typed provider need not satisfy ModelProvider; say so here
        # rather than fail deep inside the first turn.
        default: Callable[[], SessionHistoryPolicy] | None = getattr(
            self.provider, "default_history_policy", None
        )
        if not callable(default):
            raise TypeError(
                "provider must implement default_history_policy() when "
                "history_policy is not supplied"
            )
        self.history_policy = self._policy = default()

    def last_turn_usage(self, session_id: str) -> TurnUsage:
        """Usage accumulated by ``session_id``'s most recent run, failed or not.

        Per session: one backend serves every room the agent is in, and their
        turns interleave freely, so a single "most recent" tally would report
        whichever room last called the model.
        """
        return self._turn_usage.get(session_id, TurnUsage())

    def has_session(self, session_id: str) -> bool:
        """Whether ``session_id`` is already bound — unlike :meth:`session`,
        asking does not create one."""
        return session_id in self._sessions

    def session(self, session_id: str) -> list[ModelMessage]:
        """Return the mutable session list for ``session_id`` (creates if missing)."""
        return self._sessions.setdefault(session_id, [])

    def bind_session(self, session_id: str, messages: list[ModelMessage]) -> None:
        """Attach an existing list as the session store (shared with a façade)."""
        self._sessions[session_id] = messages

    def trim_session(self, session_id: str) -> int:
        """Apply the provider's history bound; return how many were dropped."""
        return self._policy.trim(self.session(session_id))

    async def start(self, context: BackendContext) -> None:
        del context

    async def close_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        self._turn_usage.pop(session_id, None)

    async def aclose(self) -> None:
        close = getattr(self.provider, "aclose", None)
        if close is not None:
            await close()

    async def run(
        self,
        *,
        session_id: str,
        message: PlatformMessage,
        context: RunContext,
        participants_context: str | None = None,
        contacts_context: str | None = None,
        tools: Sequence[Any] | None = None,
    ) -> RunResult:
        """Run one turn of the tool loop against ``session_id``'s history.

        This is not an ``AgentBackend``: the loop owns its own session, so it
        takes what it primes a turn with rather than a whole turn request with
        a history it would ignore.
        """
        session = self._sessions.setdefault(session_id, [])
        self._policy.prime_turn(
            session,
            message=message,
            participants_context=participants_context,
            contacts_context=contacts_context,
        )
        turn_usage = TurnUsage()
        self._turn_usage[session_id] = turn_usage
        delivery: DeliveryReceipt | None = None
        text_out: str | None = None
        model_ctx = ProviderModelContext(cancellation=context.cancellation)
        rounds = (
            count() if self.max_tool_rounds is None else range(self.max_tool_rounds)
        )

        for _ in rounds:
            context.cancellation.throw_if_cancelled()
            response = await self._complete(session, model_ctx, tools)
            if response.usage is not None:
                turn_usage = turn_usage + response.usage
                self._turn_usage[session_id] = turn_usage

            match response.tool_calls:
                case [] | ():
                    text_out = response.text
                    self._policy.append_final_assistant(session, response)
                    break
                case tool_calls:
                    receipt = await self._apply_tool_round(
                        session, context, response, tool_calls
                    )
                    delivery = delivery or receipt
        else:
            # Every round requested tools — no terminal text response.
            await self._handle_max_rounds(context, session_id)
        return RunResult(
            text=text_out,
            usage=None if turn_usage.is_empty else turn_usage,
            delivery=delivery,
        )

    async def _handle_max_rounds(self, context: RunContext, session_id: str) -> None:
        match self.on_max_rounds:
            case "raise":
                raise MaxToolRoundsExceeded(
                    f"Exceeded max tool rounds ({self.max_tool_rounds}) "
                    f"in room {session_id}"
                )
            case "thought":
                await context.events.emit(
                    ThoughtEvent(content="max tool rounds reached")
                )

    async def _complete(
        self,
        session: Sequence[ModelMessage],
        model_ctx: ProviderModelContext,
        tools: Sequence[Any] | None,
    ) -> ModelResponse:
        request = ModelRequest(
            messages=list(session),
            tools=tools if tools is not None else (self.tool_definitions or None),
            system=self.system or None,
            sampling=self.sampling,
            raw_options=self.raw_options,
        )
        return await self.provider.complete(request, context=model_ctx)

    async def _apply_tool_round(
        self,
        session: list[ModelMessage],
        context: RunContext,
        response: ModelResponse,
        tool_calls: Sequence[ModelToolCall],
    ) -> DeliveryReceipt | None:
        results: list[ToolRoundItem] = []
        delivery: DeliveryReceipt | None = None
        next_call_index = 0
        try:
            for next_call_index, call in enumerate(tool_calls):
                context.cancellation.throw_if_cancelled()
                await context.events.emit(
                    ToolCallEvent(
                        tool_name=call.name,
                        tool_call_id=call.id,
                        arguments=dict(call.arguments),
                    )
                )
                context.cancellation.throw_if_cancelled()
                outcome = await self._execute(context, call)
                content = _outcome_text(outcome)
                item = ToolRoundItem(call=call, outcome=outcome, content=content)
                results.append(item)
                next_call_index += 1
                receipt = receipt_from_tool_outcome(call.name, outcome)
                delivery = delivery or receipt
                await context.events.emit(
                    ToolResultEvent(
                        tool_name=call.name,
                        tool_call_id=call.id,
                        content=content,
                        status=ToolStatus.COMPLETED
                        if outcome.ok
                        else ToolStatus.FAILED,
                    )
                )
        except (asyncio.CancelledError, Exception):
            self._append_aborted_tool_round(
                session, response, results, tool_calls[next_call_index:]
            )
            raise
        self._policy.append_tool_round(session, response, results)
        return delivery

    def _append_aborted_tool_round(
        self,
        session: list[ModelMessage],
        response: ModelResponse,
        completed: Sequence[ToolRoundItem],
        unexecuted: Sequence[ModelToolCall],
    ) -> None:
        """Persist completed effects and mark the remaining calls cancelled."""
        if not completed:
            return
        cancelled_outcome = ToolCallOutcome(
            value=_CANCELLED_TOOL_CALL_MESSAGE,
            ok=False,
            error_message=_CANCELLED_TOOL_CALL_MESSAGE,
        )
        self._policy.append_tool_round(
            session,
            response,
            [
                *completed,
                *(
                    ToolRoundItem(
                        call=call,
                        outcome=cancelled_outcome,
                        content=_CANCELLED_TOOL_CALL_MESSAGE,
                    )
                    for call in unexecuted
                ),
            ],
        )

    async def _execute(
        self, context: RunContext, call: ModelToolCall
    ) -> ToolCallOutcome:
        if self.execute_override is not None:
            return await self.execute_override(context, call)
        try:
            return await context.tools.execute_tool_call_structured(
                call.name, dict(call.arguments)
            )
        except Exception as exc:
            return ToolCallOutcome(value=None, ok=False, error_message=str(exc))


def _outcome_text(outcome: ToolCallOutcome) -> str:
    match outcome:
        case ToolCallOutcome(ok=False, error_message=msg) if msg:
            return msg
        case ToolCallOutcome(ok=False, value=value) if value is not None:
            return value if isinstance(value, str) else _json_or_str(value)
        case ToolCallOutcome(value=value) if isinstance(value, str):
            return value
        case ToolCallOutcome(value=value):
            return _json_or_str(value)


def _json_or_str(value: Any) -> str:
    try:
        return json.dumps(value)
    except TypeError:
        return str(value)
