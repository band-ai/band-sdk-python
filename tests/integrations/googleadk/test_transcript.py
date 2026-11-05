"""Native ADK requests expose history truncation without logging message text."""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from band.adapters.google_adk import (
    HISTORY_CONTEXT_END,
    HISTORY_CONTEXT_START,
    GoogleADKAdapter,
    GoogleADKAdapterConfig,
)
from band.runtime.formatters import format_current_inputs
from band.testing import FakeAgentTools
from tests.framework_conformance.turnprobes import ROOM_ID, turn_input, user_message
from tests.integrations.googleadk.kit import (
    DeclineInput,
    GoogleModelPeer,
    batch,
    decline,
    request_text,
)

ADAPTER_LOGGER = "band.adapters.google_adk"
RECENT_CONTEXT = "LATEST-CONTEXT"
HISTORY_TEXT = f"Older context\n{RECENT_CONTEXT}"
FORMATTED_HISTORY = f"[Alice]: {HISTORY_TEXT}"


class TestTranscript:
    @pytest.mark.parametrize(
        ("limit", "expected_context", "expected_warnings"),
        [
            (100, FORMATTED_HISTORY, []),
            (
                20,
                RECENT_CONTEXT,
                [
                    (
                        ADAPTER_LOGGER,
                        logging.WARNING,
                        (
                            f"Room {ROOM_ID}: Transcript truncated from "
                            f"{len(FORMATTED_HISTORY)} to {len(RECENT_CONTEXT)} chars "
                            "to stay within token budget"
                        ),
                    )
                ],
            ),
        ],
        ids=["within-limit", "truncated"],
    )
    async def test_followup_reports_only_actual_context_truncation(
        self,
        google_model: GoogleModelPeer,
        caplog: pytest.LogCaptureFixture,
        limit: int,
        expected_context: str,
        expected_warnings: list[tuple[str, int, str]],
    ) -> None:
        first = user_message(HISTORY_TEXT)
        second = replace(first, id="second", content="NEXT-ASK")
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_transcript_chars=limit),
            additional_tools=[(DeclineInput, decline)],
        )
        await adapter.on_started("Agent", "An agent")
        google_model.responses.extend([batch(("decline", {})), batch(("decline", {}))])
        tools = FakeAgentTools()

        await adapter.on_event(turn_input(tools, first))
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=ADAPTER_LOGGER):
            await adapter.on_event(
                replace(turn_input(tools, second), is_session_bootstrap=False)
            )

        assert request_text(google_model.requests[-1]) == (
            f"{HISTORY_CONTEXT_START}\n{expected_context}\n"
            f"{HISTORY_CONTEXT_END}\n\n\n"
            f"{format_current_inputs([second])}"
        )
        assert [
            entry for entry in caplog.record_tuples if entry[0] == ADAPTER_LOGGER
        ] == expected_warnings
