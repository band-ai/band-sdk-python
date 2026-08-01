"""History policies prime a turn with context plus the user message."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace

import pytest

from band.core.turn.history import (
    AnthropicHistoryPolicy,
    BaseHistoryPolicy,
    DefaultHistoryPolicy,
    GeminiHistoryPolicy,
)
from band.core.contracts import (
    ModelMessage,
    ModelMessageRole,
    ModelResponse,
    ModelToolCall,
)
from band.core.turn.history import ToolRoundItem
from band.runtime.tools import ToolCallOutcome

from tests.core.contractsupport import message


def _prime(policy: object, session: list[ModelMessage]) -> None:
    """Prime one turn with a participants line and a user message.

    A policy is handed only what it seeds — the platform transcript is the
    adapter's to rehydrate, and a policy has no way to reach it.
    """
    policy.prime_turn(  # type: ignore[attr-defined]
        session,
        message=message(content="ping"),
        participants_context="Alice is here",
        contacts_context=None,
    )


def _outline(session: Sequence[ModelMessage]) -> tuple[str, ...]:
    """Readable projection of primed session content (not roles / internals)."""
    return tuple(str(entry.content) for entry in session)


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(DefaultHistoryPolicy(), id="default"),
        pytest.param(AnthropicHistoryPolicy(), id="anthropic"),
    ],
)
def test_prime_turn_seeds_context_and_user_only(policy: BaseHistoryPolicy) -> None:
    """A primed turn is exactly the system context plus the user message."""
    session: list[ModelMessage] = []
    _prime(policy, session)

    outline = _outline(session)
    assert len(outline) == 2
    assert any("Alice is here" in entry for entry in outline)
    assert any("[Ada]: ping" in entry for entry in outline)


def test_gemini_prime_turn_seeds_user_only() -> None:
    pytest.importorskip("google.genai")
    session: list[ModelMessage] = []
    _prime(GeminiHistoryPolicy(), session)

    outline = _outline(session)
    assert len(outline) == 1
    assert session[0].role is ModelMessageRole.USER
    assert "ping" in outline[0]


@pytest.mark.parametrize(
    "policy",
    [
        pytest.param(DefaultHistoryPolicy(), id="default"),
        pytest.param(AnthropicHistoryPolicy(), id="anthropic"),
    ],
)
def test_repriming_appends_a_second_turn(policy: BaseHistoryPolicy) -> None:
    """Priming is additive: a second turn extends the session, not replaces it."""
    session: list[ModelMessage] = []
    _prime(policy, session)
    _prime(policy, session)

    assert len(_outline(session)) == 4


def _user(text: str) -> ModelMessage:
    from google.genai import types

    return ModelMessage(
        role=ModelMessageRole.USER,
        content=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
    )


def _model(text: str) -> ModelMessage:
    from google.genai import types

    return ModelMessage(
        role=ModelMessageRole.ASSISTANT,
        content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
    )


class TestGeminiTrim:
    """Gemini bounds its session, and must not leave a prefix the API rejects."""

    def test_keeps_the_most_recent_messages(self) -> None:
        pytest.importorskip("google.genai")
        session = [_user(f"msg-{index}") for index in range(10)]

        dropped = GeminiHistoryPolicy(max_history_messages=5).trim(session)

        assert dropped == 5
        assert len(session) == 5
        assert session[0].content.parts[0].text == "msg-5"

    def test_is_a_noop_under_the_limit(self) -> None:
        pytest.importorskip("google.genai")
        session = [_user("hi")]

        assert GeminiHistoryPolicy(max_history_messages=50).trim(session) == 0
        assert len(session) == 1

    def test_realigns_past_a_leading_model_turn(self) -> None:
        """Gemini rejects a history that opens on a model turn."""
        pytest.importorskip("google.genai")
        session = [_user("msg-0"), _model("reply-0"), _user("msg-1"), _model("reply-1")]

        GeminiHistoryPolicy(max_history_messages=3).trim(session)

        assert [entry.content.role for entry in session] == ["user", "model"]
        assert session[0].content.parts[0].text == "msg-1"

    def test_strips_tool_responses_orphaned_by_the_slice(self) -> None:
        """The matching function_call was trimmed away, so the response must go."""
        pytest.importorskip("google.genai")
        from google.genai import types

        orphaned = ModelMessage(
            role=ModelMessageRole.USER,
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="tc_1",
                            name="band_send_message",
                            response={"output": {"status": "sent"}},
                        )
                    ),
                    types.Part.from_text(text="[Alice]: follow-up"),
                ],
            ),
        )
        session = [_user("msg-0"), _model("call"), orphaned, _model("reply-1")]

        GeminiHistoryPolicy(max_history_messages=3).trim(session)

        assert len(session) == 2
        kept = session[0].content
        assert kept.role == "user"
        assert [part.function_response for part in kept.parts] == [None]
        assert kept.parts[0].text == "[Alice]: follow-up"


class TestAnthropicToolRoundReplay:
    """What gets replayed to Anthropic after a tool round."""

    @staticmethod
    def _response_with_thinking() -> ModelResponse:
        from anthropic.types import TextBlock, ThinkingBlock, ToolUseBlock

        return ModelResponse(
            tool_calls=(ModelToolCall(id="tu_1", name="greet", arguments={}),),
            raw=SimpleNamespace(
                content=[
                    ThinkingBlock(
                        type="thinking", thinking="working it out", signature="sig-1"
                    ),
                    TextBlock(type="text", text="on it"),
                    ToolUseBlock(
                        type="tool_use", id="tu_1", name="greet", input={"name": "Ada"}
                    ),
                ]
            ),
        )

    def test_thinking_blocks_survive_the_replay(self) -> None:
        """Anthropic rejects a replayed turn whose thinking blocks were dropped.

        Their signatures are how it verifies the turn it is handed back, so a
        tool round that reconstructs only text and tool_use returns a 400 on
        the next round.
        """
        pytest.importorskip("anthropic")
        session: list[ModelMessage] = []

        AnthropicHistoryPolicy().append_tool_round(
            session,
            self._response_with_thinking(),
            [
                ToolRoundItem(
                    call=ModelToolCall(id="tu_1", name="greet", arguments={}),
                    outcome=ToolCallOutcome(value="hello Ada", ok=True),
                    content="hello Ada",
                )
            ],
        )

        assistant = session[0].content
        kinds = [block["type"] for block in assistant]
        assert kinds == ["thinking", "text", "tool_use"]
        assert assistant[0]["signature"] == "sig-1"

    def test_the_tool_result_turn_follows_the_assistant_turn(self) -> None:
        pytest.importorskip("anthropic")
        session: list[ModelMessage] = []

        AnthropicHistoryPolicy().append_tool_round(
            session,
            self._response_with_thinking(),
            [
                ToolRoundItem(
                    call=ModelToolCall(id="tu_1", name="greet", arguments={}),
                    outcome=ToolCallOutcome(value="boom", ok=False),
                    content="boom",
                )
            ],
        )

        assert [entry.role for entry in session] == [
            ModelMessageRole.ASSISTANT,
            ModelMessageRole.USER,
        ]
        result = session[1].content[0]
        assert result["tool_use_id"] == "tu_1"
        assert result["is_error"] is True

    def test_empty_text_blocks_are_left_out(self) -> None:
        """Anthropic rejects an empty text block, so replaying one 400s the turn.

        Guard against the obvious over-correction: preserving every block to
        keep thinking must not also start replaying empty text.
        """
        pytest.importorskip("anthropic")
        from anthropic.types import TextBlock, ToolUseBlock

        response = ModelResponse(
            tool_calls=(ModelToolCall(id="tu_1", name="greet", arguments={}),),
            raw=SimpleNamespace(
                content=[
                    TextBlock(type="text", text="   "),
                    ToolUseBlock(type="tool_use", id="tu_1", name="greet", input={}),
                ]
            ),
        )
        session: list[ModelMessage] = []

        AnthropicHistoryPolicy().append_tool_round(
            session,
            response,
            [
                ToolRoundItem(
                    call=ModelToolCall(id="tu_1", name="greet", arguments={}),
                    outcome=ToolCallOutcome(value="ok", ok=True),
                    content="ok",
                )
            ],
        )

        assert [block["type"] for block in session[0].content] == ["tool_use"]
