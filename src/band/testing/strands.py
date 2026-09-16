"""Scripted Strands model for offline adapter tests.

Strands ships no test provider, so tests that want to drive the real adapter and
the framework's own agent loop without inference implement the documented
``strands.models.Model`` ABC. This is that implementation, shared so every test
scripts turns the same way.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass, field
from typing import Any

try:
    from strands.models import Model
    from strands.types.content import Messages
    from strands.types.streaming import StreamEvent
    from strands.types.tools import ToolSpec
except ImportError as error:
    raise ImportError(
        "Strands Agents dependencies not installed. "
        "Install with: uv add band-sdk[strands]"
    ) from error


@dataclass(frozen=True)
class ToolTurn:
    """A scripted turn that calls ``name`` with ``args``."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextTurn:
    """A scripted turn that answers in plain text and ends the run."""

    text: str


@dataclass(frozen=True)
class ErrorTurn:
    """A scripted turn where the provider call fails instead of answering."""

    error: Exception


ScriptedTurn = ToolTurn | TextTurn | ErrorTurn


class ScriptedStrandsModel(Model):
    """Replay scripted turns in place of a provider call.

    Each ``stream()`` call pops the next turn and yields the Converse
    ``StreamEvent`` sequence Strands' event loop parses; once the script is
    exhausted every further call ends the run with plain text. An ``ErrorTurn``
    fails the provider call instead, so a test can drive the adapter's failure
    path. Non-zero token counts add the optional trailing ``metadata`` event, so
    a test can exercise usage accumulation across a turn's model calls.
    """

    def __init__(
        self,
        turns: Sequence[ScriptedTurn],
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._turns = list(turns)
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._config: dict[str, Any] = {}

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    def get_config(self) -> Any:
        return self._config

    async def structured_output(
        self,
        output_model: Any,
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("the scripted model does not do structured output")
        yield {}  # pragma: no cover - makes this an async generator

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent, None]:
        turn = self._turns.pop(0) if self._turns else TextTurn("done")
        if isinstance(turn, ErrorTurn):
            raise turn.error
        yield {"messageStart": {"role": "assistant"}}
        match turn:
            case ToolTurn(name=name, args=args):
                yield {
                    "contentBlockStart": {
                        "start": {
                            "toolUse": {"toolUseId": f"call-{name}", "name": name}
                        }
                    }
                }
                yield {
                    "contentBlockDelta": {
                        "delta": {"toolUse": {"input": json.dumps(args)}}
                    }
                }
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "tool_use"}}
            case TextTurn(text=text):
                yield {"contentBlockStart": {"start": {}}}
                yield {"contentBlockDelta": {"delta": {"text": text}}}
                yield {"contentBlockStop": {}}
                yield {"messageStop": {"stopReason": "end_turn"}}
        if self._input_tokens or self._output_tokens:
            yield {
                "metadata": {
                    "usage": {
                        "inputTokens": self._input_tokens,
                        "outputTokens": self._output_tokens,
                        "totalTokens": self._input_tokens + self._output_tokens,
                    },
                    "metrics": {"latencyMs": 1},
                }
            }
