"""NativeHTTPRecovery behavior of the Google ADK adapter."""

from __future__ import annotations

import asyncio
import logging

import pytest
from google.genai import types
from google.genai.errors import APIError

from band.core.protocols import (
    GENERIC_PROVIDER_FAILURE_MESSAGE,
)
from band.core.types import Emit
from band.runtime.tools import BandTool
from band.testing import (
    failure_reports,
)
from tests.adapters.genaikit import (
    SCRIPTED_USAGE,
    text_reply,
    tool_call,
)
from tests.adapters.googleadk.bridge import GoogleADKAdapter, GoogleADKAdapterConfig
from tests.adapters.googleadk.httppeer import (
    EFFECT_ARGS,
    LOCAL_API_KEY,
    GoogleHTTPPeer,
    HTTPMode,
    RecoveryTools,
)
from tests.adapters.usage_events import recorded_usage_payloads
from tests.framework_conformance.turnprobes import (
    turn_input,
)


@pytest.mark.parametrize("google_http", [HTTPMode.MOCK, HTTPMode.TCP], indirect=True)
class TestNativeHTTPRecovery:
    @pytest.mark.parametrize(
        ("responses", "max_retries", "expected_calls", "error_code"),
        [
            pytest.param(
                [
                    503,
                    tool_call(
                        BandTool.SEND_MESSAGE,
                        {"content": "Recovered", "mentions": ["@alice"]},
                    ),
                    text_reply("Done"),
                ],
                2,
                3,
                None,
                id="recover",
            ),
            pytest.param([503, 503, 503], 2, 3, 503, id="exhaust"),
            pytest.param([503], 0, 1, 503, id="disabled"),
            pytest.param([400], 2, 1, 400, id="nonretryable"),
        ],
    )
    @pytest.mark.asyncio
    async def test_generation_budget_and_failure_reporting(
        self,
        google_http: GoogleHTTPPeer,
        responses: list[int | types.GenerateContentResponse],
        max_retries: int,
        expected_calls: int,
        error_code: int | None,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        google_http.responses = responses.copy()
        adapter = GoogleADKAdapter(
            GoogleADKAdapterConfig(max_retries=max_retries), emit={Emit.USAGE}
        )
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        with caplog.at_level(logging.INFO, logger="google_genai._api_client"):
            if error_code is None:
                await adapter.on_event(turn_input(tools))
                assert failure_reports(tools) == []
                assert tools.chat == ["Recovered"]
                [usage] = recorded_usage_payloads(tools)
                assert (usage["input_tokens"], usage["output_tokens"]) == (
                    SCRIPTED_USAGE.prompt_token_count * 2,
                    SCRIPTED_USAGE.candidates_token_count * 2,
                )
            else:
                with pytest.raises(APIError) as raised:
                    await adapter.on_event(turn_input(tools))
                assert raised.value.code == error_code
                assert failure_reports(tools) == [
                    ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
                ]
                assert tools.chat == []
                assert recorded_usage_payloads(tools) == []
        assert len(google_http.requests) == expected_calls
        assert google_http.clients_closed
        repeated = (
            google_http.generation_requests[:2]
            if error_code is None
            else google_http.generation_requests
        )
        assert repeated == [repeated[0]] * len(repeated)
        retry_logs = [
            r.getMessage()
            for r in caplog.records
            if r.name == "google_genai._api_client"
            and r.getMessage().startswith("Retrying ")
        ]
        assert len(retry_logs) == (1 if error_code is None else expected_calls - 1)
        for entry in retry_logs:
            assert "ServerError" in entry and "503" in entry
            assert LOCAL_API_KEY not in entry
            assert "vault code" not in entry and "Completed effect" not in entry

    @pytest.mark.parametrize("exhausted", [False, True], ids=["recover", "exhaust"])
    @pytest.mark.asyncio
    async def test_post_tool_recovery_never_replays_the_effect(
        self, google_http: GoogleHTTPPeer, exhausted: bool
    ) -> None:
        google_http.responses = [
            tool_call(
                BandTool.SEND_MESSAGE,
                EFFECT_ARGS,
            ),
            503,
            *([503, 503] if exhausted else [text_reply("Done")]),
        ]
        adapter = GoogleADKAdapter(emit={Emit.USAGE})
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        if exhausted:
            with pytest.raises(APIError):
                await adapter.on_event(turn_input(tools))
            assert failure_reports(tools) == [
                ("google_adk", GENERIC_PROVIDER_FAILURE_MESSAGE)
            ]
        else:
            await adapter.on_event(turn_input(tools))
            assert failure_reports(tools) == []
        assert tools.chat == ["Completed effect"]
        assert google_http.clients_closed
        assert tools.tool_calls == [
            {
                "tool_name": BandTool.SEND_MESSAGE,
                "arguments": EFFECT_ARGS,
            }
        ]
        [usage] = recorded_usage_payloads(tools)
        successful_responses = 1 if exhausted else 2
        assert (usage["input_tokens"], usage["output_tokens"]) == (
            SCRIPTED_USAGE.prompt_token_count * successful_responses,
            SCRIPTED_USAGE.candidates_token_count * successful_responses,
        )
        requests = google_http.generation_requests
        assert len(requests) == (4 if exhausted else 3)
        assert requests[1:] == [requests[1]] * (len(requests) - 1)
        assert any(
            "functionResponse" in part
            for content in requests[1]["contents"]
            for part in content["parts"]
        )

    @pytest.mark.asyncio
    async def test_cancellation_during_backoff_stops_generation(
        self, google_http: GoogleHTTPPeer
    ) -> None:
        google_http.responses = [
            tool_call(
                BandTool.SEND_MESSAGE,
                EFFECT_ARGS,
            ),
            503,
        ]
        google_http.initial_delay = 60
        google_http.max_delay = 60
        adapter = GoogleADKAdapter(emit={Emit.USAGE})
        await adapter.on_started("TestBot", "Test bot")
        tools = RecoveryTools()
        task = asyncio.create_task(adapter.on_event(turn_input(tools)))
        try:
            await asyncio.wait_for(google_http.retry_started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert len(google_http.requests) == 2
        assert google_http.clients_closed
        assert tools.tool_calls == [
            {
                "tool_name": BandTool.SEND_MESSAGE,
                "arguments": EFFECT_ARGS,
            }
        ]
        assert failure_reports(tools) == []
        assert tools.chat == ["Completed effect"]
        # Cancellation suppresses best-effort usage emission by contract.
        assert recorded_usage_payloads(tools) == []
