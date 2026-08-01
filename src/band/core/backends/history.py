"""Per-provider session history policies for ``NativeToolLoopBackend``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from band.core.contracts import (
    ModelMessage,
    ModelMessageRole,
    ModelResponse,
    ModelToolCall,
)
from band.core.types import PlatformMessage
from band.runtime.tools import ToolCallOutcome


@dataclass(frozen=True)
class ToolRoundItem:
    """One tool call plus its execution outcome within a tool round."""

    call: ModelToolCall
    outcome: ToolCallOutcome
    content: str


class SessionHistoryPolicy(Protocol):
    """How a provider-shaped session is primed and extended during a tool loop."""

    def prime_turn(
        self,
        session: list[ModelMessage],
        *,
        message: PlatformMessage,
        participants_context: str | None,
        contacts_context: str | None,
    ) -> None: ...

    def append_final_assistant(
        self, session: list[ModelMessage], response: ModelResponse
    ) -> None: ...

    def append_tool_round(
        self,
        session: list[ModelMessage],
        response: ModelResponse,
        results: Sequence[ToolRoundItem],
    ) -> None: ...

    def trim(self, session: list[ModelMessage]) -> int:
        """Bound ``session`` in place; return how many entries were dropped."""
        ...


@dataclass
class BaseHistoryPolicy:
    """Shared system-context + user-turn priming for string histories.

    Seeding a bootstrap session is the adapter's job, not a policy's: only the
    adapter has the framework converter that renders sender prefixes, folds
    tool events and drops the agent's own messages. A policy sees the raw
    platform rows, so anything it seeded would be a thinner second answer to a
    question already answered.
    """

    def trim(self, session: list[ModelMessage]) -> int:
        """Unbounded by default — only Gemini caps its history today."""
        return 0

    def prime_turn(
        self,
        session: list[ModelMessage],
        *,
        message: PlatformMessage,
        participants_context: str | None,
        contacts_context: str | None,
    ) -> None:
        session.extend(_system_context_messages(participants_context, contacts_context))
        session.append(
            ModelMessage(
                role=ModelMessageRole.USER,
                content=message.format_for_llm(),
            )
        )


@dataclass
class DefaultHistoryPolicy(BaseHistoryPolicy):
    """Provider-neutral string/tool-role history (native contract tests)."""

    def append_final_assistant(
        self, session: list[ModelMessage], response: ModelResponse
    ) -> None:
        if response.text:
            session.append(
                ModelMessage(role=ModelMessageRole.ASSISTANT, content=response.text)
            )

    def append_tool_round(
        self,
        session: list[ModelMessage],
        response: ModelResponse,
        results: Sequence[ToolRoundItem],
    ) -> None:
        session.append(
            ModelMessage(
                role=ModelMessageRole.ASSISTANT,
                content=response.text,
                name="tool_calls",
            )
        )
        for item in results:
            session.append(
                ModelMessage(
                    role=ModelMessageRole.TOOL,
                    content=item.content,
                    tool_call_id=item.call.id,
                    name=item.call.name,
                )
            )


@dataclass
class AnthropicHistoryPolicy(BaseHistoryPolicy):
    """Anthropic-shaped history: rich content blocks + batched tool_result user turn."""

    def append_final_assistant(
        self, session: list[ModelMessage], response: ModelResponse
    ) -> None:
        text = response.text
        if text:
            session.append(ModelMessage(role=ModelMessageRole.ASSISTANT, content=text))

    def append_tool_round(
        self,
        session: list[ModelMessage],
        response: ModelResponse,
        results: Sequence[ToolRoundItem],
    ) -> None:
        session.append(
            ModelMessage(
                role=ModelMessageRole.ASSISTANT,
                content=_anthropic_assistant_content(response),
            )
        )
        session.append(
            ModelMessage(
                role=ModelMessageRole.USER,
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": item.call.id,
                        "content": item.content,
                        "is_error": not item.outcome.ok,
                    }
                    for item in results
                ],
            )
        )


@dataclass
class GeminiHistoryPolicy:
    """Gemini-shaped history: merged user turns + ``types.Content`` objects."""

    max_history_messages: int = 200

    def trim(self, session: list[ModelMessage]) -> int:
        """Bound ``session`` in place, returning how many entries were dropped.

        Gemini rejects a history that opens on a model turn, or on a user turn
        whose leading parts are ``function_response``s whose matching call was
        just trimmed away. So after slicing, realign to the next usable user
        turn rather than leaving a prefix the API will refuse.
        """
        if len(session) <= self.max_history_messages:
            return 0

        dropped = len(session) - self.max_history_messages
        del session[:dropped]

        while session:
            first = session[0]
            if first.role is ModelMessageRole.ASSISTANT:
                session.pop(0)
                dropped += 1
                continue
            realigned = _without_leading_tool_responses(first.content)
            if realigned is None:
                session.pop(0)
                dropped += 1
                continue
            session[0] = ModelMessage(role=first.role, content=realigned)
            break

        return dropped

    def prime_turn(
        self,
        session: list[ModelMessage],
        *,
        message: PlatformMessage,
        participants_context: str | None,
        contacts_context: str | None,
    ) -> None:
        from google.genai import types

        user_parts: list[Any] = []
        if participants_context:
            user_parts.append(
                types.Part.from_text(text=f"[System]: {participants_context}")
            )
        if contacts_context:
            user_parts.append(
                types.Part.from_text(text=f"[System]: {contacts_context}")
            )
        user_parts.append(types.Part.from_text(text=message.format_for_llm()))
        session.append(
            ModelMessage(
                role=ModelMessageRole.USER,
                content=types.Content(role="user", parts=user_parts),
            )
        )

    def append_final_assistant(
        self, session: list[ModelMessage], response: ModelResponse
    ) -> None:
        content = _gemini_candidate_content(response)
        if content is not None:
            session.append(
                ModelMessage(role=ModelMessageRole.ASSISTANT, content=content)
            )

    def append_tool_round(
        self,
        session: list[ModelMessage],
        response: ModelResponse,
        results: Sequence[ToolRoundItem],
    ) -> None:
        from google.genai import types

        content = _gemini_candidate_content(response)
        if content is not None:
            session.append(
                ModelMessage(role=ModelMessageRole.ASSISTANT, content=content)
            )
        parts = []
        for item in results:
            payload = (
                {"error": item.content}
                if not item.outcome.ok
                else {"output": item.content}
            )
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=item.call.id,
                        name=item.call.name,
                        response=payload,
                    )
                )
            )
        if parts:
            session.append(
                ModelMessage(
                    role=ModelMessageRole.USER,
                    content=types.Content(role="user", parts=parts),
                )
            )


def _without_leading_tool_responses(content: Any) -> Any | None:
    """Drop orphaned leading ``function_response`` parts from a user turn.

    ``None`` when nothing but responses remain — the turn is unusable and the
    caller should drop it.
    """
    from google.genai import types

    if not isinstance(content, types.Content):
        return content
    parts = list(content.parts or [])
    kept = 0
    while kept < len(parts) and parts[kept].function_response is not None:
        kept += 1
    if kept == 0:
        return content
    remaining = parts[kept:]
    if not remaining:
        return None
    return types.Content(role=content.role, parts=remaining)


def _system_context_messages(
    participants_context: str | None, contacts_context: str | None
) -> list[ModelMessage]:
    return [
        ModelMessage(role=ModelMessageRole.USER, content=f"[System]: {text}")
        for text in (participants_context, contacts_context)
        if text
    ]


def _anthropic_assistant_content(response: ModelResponse) -> Any:
    """Replay the assistant turn as Anthropic produced it, block for block.

    Re-deriving only the blocks the tool loop cares about drops the rest —
    and Anthropic rejects a turn whose extended thinking is replayed without
    its thinking blocks, since their signatures are what let it verify the
    turn it is being handed back. So keep every block, whatever its type.
    """
    raw = response.raw
    content = getattr(raw, "content", None)
    if not content:
        return response.text
    kept = [_as_content_block(block) for block in content if not _is_empty_text(block)]
    return kept or response.text


def _is_empty_text(block: Any) -> bool:
    """An empty text block — Anthropic rejects one on the way back in."""
    if getattr(block, "type", None) != "text":
        return False
    return not (getattr(block, "text", "") or "").strip()


def _as_content_block(block: Any) -> Any:
    """A block in wire shape; SDK models know how to render themselves."""
    dump = getattr(block, "model_dump", None)
    return dump(exclude_none=True) if callable(dump) else block


def _gemini_candidate_content(response: ModelResponse) -> Any | None:
    from google.genai import types

    raw = response.raw
    if raw is None:
        return None
    candidates = getattr(raw, "candidates", None) or []
    if candidates and getattr(candidates[0], "content", None):
        return candidates[0].content

    function_calls = list(getattr(raw, "function_calls", None) or [])
    if not function_calls and response.tool_calls:
        function_calls = list(response.tool_calls)

    parts: list[Any] = []
    for index, call in enumerate(function_calls):
        if isinstance(call, ModelToolCall):
            call_id, call_name, args = call.id, call.name, dict(call.arguments)
        else:
            call_id = getattr(call, "id", None) or f"gemini_tool_call_{index}"
            call_name = getattr(call, "name", None) or ""
            args = dict(getattr(call, "args", None) or {})
        if call_name:
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=call_id, name=call_name, args=args
                    )
                )
            )
    if not parts:
        return None
    return types.Content(role="model", parts=parts)
