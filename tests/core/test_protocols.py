"""Tests for shared AgentToolsProtocol helpers."""

from __future__ import annotations

import logging

import pytest
from band_sdk_core import AgentFailure

from band.core.exceptions import ProviderRunError
from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
    generic_provider_failure,
    send_event_safe,
    to_failure_event,
)
from band.testing.fake_tools import FakeAgentTools


class TestToFailureEvent:
    """``to_failure_event`` is the one place both SDKs put the failure ->
    room-event shape, so its parity string and metadata key are load-bearing."""

    def test_carries_the_message_and_failure_metadata(self) -> None:
        failure = AgentFailure("codex", "boom", "timeout", {"http_status": 500})

        content, metadata = to_failure_event(failure)

        assert content == "boom"
        assert metadata == {
            "failure": {
                "provider": "codex",
                "code": "timeout",
                "message": "boom",
                "detail": {"http_status": 500},
            }
        }

    @pytest.mark.parametrize("blank_message", ["", "   ", "\n\t"])
    def test_blank_message_falls_back_to_a_generic_one(
        self, blank_message: str
    ) -> None:
        """A provider message can arrive blank -- the platform rejects a blank
        chat event, so an unguarded blank message would make the failure
        vanish from the room entirely. The fallback string must match TS's
        ``toFailureEvent`` exactly for cross-SDK parity."""
        content, _metadata = to_failure_event(AgentFailure("acp", blank_message))

        assert content == "acp failed without an error message."

    def test_generic_fallback_has_no_code_or_detail(self) -> None:
        """A provider/adapter that gives no structured signal at all still
        produces a valid failure -- code and detail default to None rather
        than an invented value."""
        content, metadata = to_failure_event(AgentFailure("anthropic", "boom"))

        assert content == "boom"
        assert metadata["failure"]["code"] is None
        assert metadata["failure"]["detail"] is None


class TestGenericProviderFailure:
    """Provider text can echo the prompt or carry secrets, so the room only
    ever sees the generic message and, for a failure returned as data, its
    coarse code."""

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            pytest.param(ProviderRunError("SAFETY", "the secret is 42"), "SAFETY"),
            pytest.param(RuntimeError("the secret is 42"), None),
        ],
    )
    def test_posts_only_the_generic_message_and_coarse_code(
        self, error: Exception, code: str | None
    ) -> None:
        failure = generic_provider_failure("gemini", error)

        assert failure.to_dict() == {
            "provider": "gemini",
            "code": code,
            "message": GENERIC_PROVIDER_FAILURE_MESSAGE,
            "detail": None,
        }


class TestSendEventSafe:
    """send_event_safe is the shared best-effort event sender every migrated
    adapter's non-critical telemetry (thoughts, task/lifecycle markers) goes
    through -- a regression here silently drops events across every one of
    them."""

    async def test_forwards_a_successful_send_and_returns_true(self) -> None:
        tools = FakeAgentTools()

        sent = await send_event_safe(tools, "hello", "thought")

        assert sent is True
        assert tools.events_sent[0]["content"] == "hello"

    async def test_swallows_a_send_event_failure_and_logs_at_the_given_level(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        tools = FakeAgentTools()
        tools.send_event_error = RuntimeError("platform rejected the event")

        with caplog.at_level(logging.DEBUG, logger="band.core.protocols"):
            sent = await send_event_safe(
                tools,
                "hello",
                "task",
                log_label="widget event",
                log_level=logging.DEBUG,
            )

        assert sent is False
        assert tools.events_sent == []
        record = next(r for r in caplog.records if r.name == "band.core.protocols")
        assert record.levelno == logging.DEBUG
        assert "widget event" in record.message

    async def test_default_log_level_is_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        tools = FakeAgentTools()
        tools.send_event_error = RuntimeError("boom")

        with caplog.at_level(logging.WARNING, logger="band.core.protocols"):
            await send_event_safe(tools, "hello", "task")

        record = next(r for r in caplog.records if r.name == "band.core.protocols")
        assert record.levelno == logging.WARNING
