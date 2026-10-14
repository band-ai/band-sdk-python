"""EmptyFinalAnswer behavior for Pydantic AI."""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic_ai import UnexpectedModelBehavior, _tool_execution
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from band.adapters.pydantic_ai import (
    OUTPUT_RETRIES_EXHAUSTED,
    PydanticAIAdapter,
    PydanticAIAdapterConfig,
    _is_output_retries_exhausted,
)
from band.core.exceptions import BandToolError
from band.core.protocols import GENERIC_PROVIDER_FAILURE_MESSAGE
from band.core.types import Emit, PlatformMessage
from band.testing import FakeAgentTools, failure_reports, reported_failures
from tests.adapters.pydanticai.kit import (
    _run_on_message,
    make_raising_stream,
    make_usage_response,
)
from tests.adapters.usage_events import sent_usage_payloads


class TestEmptyFinalAnswer:
    """A model can end a turn with output pydantic-ai cannot accept — blank text,
    say — after the agent already replied/acted via tools, exhausting the refused
    output-retry budget. That is benign — the work already went out — so it must not
    fail the message, but a genuine no-work failure must still surface.
    """

    def test_swallow_matches_the_wording_pydantic_ai_actually_raises(self) -> None:
        """The swallow keys on message text, since pydantic-ai exposes no code for it.

        Every other test here builds the exception by hand, so a reword upstream
        would leave them green while the swallow quietly stopped matching — which is
        exactly what 2.x did to the 1.x phrasing ("Exceeded maximum retries (N) for
        output validation"). Read the real source so a future reword fails here.
        """

        source = Path(_tool_execution.__file__).read_text(encoding="utf-8").lower()
        assert OUTPUT_RETRIES_EXHAUSTED in source
        assert _is_output_retries_exhausted(
            UnexpectedModelBehavior("Exceeded maximum output retries (3)")
        )
        assert not _is_output_retries_exhausted(
            UnexpectedModelBehavior("Invalid response, unable to find output")
        )

    @pytest.mark.asyncio
    async def test_empty_output_after_tool_is_benign(
        self, sample_message, mock_pydantic_agent
    ):
        """Output-retry exhaustion after the turn replied is swallowed."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")
        tools = FakeAgentTools(room_id="room-123")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Exceeded maximum output retries (1)"),
                work=lambda: tools.send_message("hi", ["@Alice"]),
            )
        )

        # Must not raise: the reply already went out via the tool this turn.
        await adapter.on_message(
            msg=sample_message,
            tools=tools,
            history=[],
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-123",
        )

        # Regression (fallback path): with the run mocked, capture_run_messages records
        # nothing, so the swallow falls back to preserving at least the user prompt so
        # the next same-session turn isn't amnesiac.

        preserved = adapter._message_history["room-123"]
        assert preserved, "swallowed turn should still record the user message"
        assert isinstance(preserved[-1], ModelRequest)
        assert any(
            isinstance(part, UserPromptPart) and "Hello, agent!" in str(part.content)
            for part in preserved[-1].parts
        )
        assert reported_failures(tools) == []

    @pytest.mark.asyncio
    async def test_empty_output_preserves_full_captured_turn(
        self, sample_message, mock_pydantic_agent
    ):
        """The swallow persists the whole captured turn — not just the user prompt —
        so a later 'what did you just say?' has the agent's reply in context."""

        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")
        tools = FakeAgentTools(room_id="room-xyz")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Exceeded maximum output retries (1)"),
                work=lambda: tools.send_message("hi", ["@Alice"]),
            )
        )

        # pydantic-ai populates capture_run_messages during a real run; simulate a
        # run that captured the full turn (user prompt + the agent's response).
        full_turn = [
            ModelRequest(parts=[UserPromptPart(content="[Alice]: hi")]),
            ModelResponse(parts=[TextPart(content="replied via tool")]),
        ]

        @contextmanager
        def fake_capture():
            yield full_turn

        with patch("band.adapters.pydantic_ai.capture_run_messages", fake_capture):
            await adapter.on_message(
                msg=sample_message,
                tools=tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-xyz",
            )

        preserved = adapter._message_history["room-xyz"]
        # The full turn is kept — crucially the assistant response, not only the user.
        assert preserved == full_turn
        assert any(isinstance(message, ModelResponse) for message in preserved)

    @pytest.mark.asyncio
    async def test_empty_output_without_tool_propagates(
        self, sample_message, mock_pydantic_agent
    ):
        """Same error with no tool executed is a real failure — propagate."""
        await self._assert_exhaustion_propagates(
            sample_message, mock_pydantic_agent, work=None
        )

    @pytest.mark.asyncio
    async def test_failed_run_still_emits_captured_usage(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """A run that raises still emits the usage its captured responses accrued.

        Tokens spent before the failure were still spent: the finally-based emit
        falls back to summing this run's captured ModelResponses when no result
        event fired, so a hard mid-run failure doesn't silently drop usage."""

        adapter = PydanticAIAdapter(
            PydanticAIAdapterConfig(model="openai:gpt-5.4"), emit=Emit.USAGE
        )
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Invalid response, unable to find output"),
            )
        )

        # Simulate a run that captured a response with usage before raising.
        captured_turn = [
            ModelRequest(parts=[UserPromptPart(content="[Alice]: hi")]),
            make_usage_response(100, 20, parts=[TextPart(content="partial")]),
        ]

        @contextmanager
        def fake_capture():
            yield captured_turn

        with (
            patch("band.adapters.pydantic_ai.capture_run_messages", fake_capture),
            pytest.raises(UnexpectedModelBehavior),
        ):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        usage_payloads = sent_usage_payloads(mock_tools)
        assert usage_payloads == [
            {
                "input_tokens": 100,
                "output_tokens": 20,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }
        ], f"expected the captured run's usage to be emitted, got {usage_payloads}"

    @pytest.mark.asyncio
    async def test_unrelated_model_error_propagates_even_after_tool(
        self, sample_message, mock_pydantic_agent
    ):
        """The swallow is narrow: other model errors still surface after a reply."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")
        tools = FakeAgentTools(room_id="room-123")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Invalid response, unable to find output"),
                work=lambda: tools.send_message("hi", ["@Alice"]),
            )
        )

        with pytest.raises(UnexpectedModelBehavior):
            await _run_on_message(adapter, sample_message, tools)

        assert failure_reports(tools) == [
            ("pydantic_ai", GENERIC_PROVIDER_FAILURE_MESSAGE)
        ]

    @pytest.mark.asyncio
    async def test_generic_provider_error_reports_and_propagates(
        self, sample_message, mock_tools, mock_pydantic_agent
    ):
        """A failure that isn't UnexpectedModelBehavior at all (a raw provider/API
        error) must still surface as a failure and propagate, not vanish uncaught."""
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")

        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                RuntimeError("provider connection reset"),
            )
        )

        with pytest.raises(RuntimeError, match="provider connection reset"):
            await adapter.on_message(
                msg=sample_message,
                tools=mock_tools,
                history=[],
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-123",
            )

        mock_tools.send_failure.assert_awaited_once()
        failure = mock_tools.send_failure.call_args.args[0]
        assert failure.provider == "pydantic_ai"
        assert failure.message == GENERIC_PROVIDER_FAILURE_MESSAGE

    @pytest.mark.asyncio
    async def test_empty_output_after_read_only_tool_propagates(
        self, sample_message, mock_pydantic_agent
    ):
        """A read-only lookup is not terminal work — output-validation exhaustion
        after only a lookup is a genuine no-response failure and must propagate."""
        tools = FakeAgentTools(room_id="room-123")

        await self._assert_exhaustion_propagates(
            sample_message, mock_pydantic_agent, tools=tools, work=tools.lookup_peers
        )

    @pytest.mark.asyncio
    async def test_empty_output_after_failed_band_tool_propagates(
        self, sample_message, mock_pydantic_agent
    ):
        """A band tool that raised did no work — exhausting output validation
        afterward is a genuine failure and must propagate."""
        tools = FakeAgentTools(room_id="room-123")

        async def rejected_send() -> None:
            # The band tool wrapper turns this raise into an error string for
            # the model; the turn records nothing either way.
            with contextlib.suppress(BandToolError):
                await tools.send_message("hi", mentions=[])

        await self._assert_exhaustion_propagates(
            sample_message, mock_pydantic_agent, tools=tools, work=rejected_send
        )

    @staticmethod
    async def _assert_exhaustion_propagates(
        sample_message: PlatformMessage,
        mock_pydantic_agent: MagicMock,
        *,
        work: Callable[[], Awaitable[object]] | None,
        tools: FakeAgentTools | None = None,
    ) -> None:
        """Output-retry exhaustion after ``work`` fails the turn as a provider error."""
        tools = tools or FakeAgentTools(room_id="room-123")
        adapter = PydanticAIAdapter(PydanticAIAdapterConfig(model="openai:gpt-5.4"))
        with patch.object(adapter, "_create_agent", return_value=mock_pydantic_agent):
            await adapter.on_started("TestBot", "Test bot")
        adapter._agent.run_stream_events = MagicMock(
            return_value=make_raising_stream(
                UnexpectedModelBehavior("Exceeded maximum output retries (1)"),
                work=work,
            )
        )

        with pytest.raises(UnexpectedModelBehavior):
            await _run_on_message(adapter, sample_message, tools)

        assert failure_reports(tools) == [
            ("pydantic_ai", GENERIC_PROVIDER_FAILURE_MESSAGE)
        ]
