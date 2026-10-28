from __future__ import annotations

import httpx
import pytest

from band.adapters.opencode import OpencodeAdapterConfig
from band.integrations.opencode.types import OpencodeSessionState
from band.testing import reported_failures
from tests.adapters.dispatch import deliver_message
from tests.adapters.opencode.helpers import (
    AnyHTTPStatusError,
    FakeOpencodeClient,
    event_message_updated,
    event_session_idle,
    event_text_part,
    idle_client,
    make_platform_message,
    run_single_turn,
    tools_protocol,
)


class TestPrompts:
    async def test_forwards_configured_model_and_variant(
        self, make_adapter, tools
    ) -> None:
        fake_client = idle_client()
        adapter = make_adapter(
            fake_client,
            config=OpencodeAdapterConfig(
                provider_id="openai",
                model_id="gpt-5",
                variant="high",
            ),
        )

        await run_single_turn(adapter, tools)

        prompt_configuration = {
            "model": fake_client.prompt_calls[0]["model"],
            "variant": fake_client.prompt_calls[0]["variant"],
        }
        assert prompt_configuration == {
            "model": {"providerID": "openai", "modelID": "gpt-5"},
            "variant": "high",
        }

    @pytest.mark.parametrize(
        ("provider_id", "model_id"),
        [("openai", None), (None, "gpt-5")],
    )
    async def test_omits_model_for_incomplete_configuration(
        self, make_adapter, tools, provider_id: str | None, model_id: str | None
    ) -> None:
        fake_client = idle_client()
        adapter = make_adapter(
            fake_client,
            config=OpencodeAdapterConfig(
                provider_id=provider_id,
                model_id=model_id,
            ),
        )

        await run_single_turn(adapter, tools)

        assert fake_client.prompt_calls[0]["model"] is None

    async def test_prompt_submission_failure_does_not_leave_room_stuck(
        self, make_adapter, tools
    ) -> None:
        fake_client = FakeOpencodeClient(
            prompt_event_sequences=[
                [
                    event_message_updated("sess-2", "msg-5"),
                    event_text_part("sess-2", "msg-5", "Recovered after failure"),
                    event_session_idle("sess-2"),
                ]
            ],
            prompt_exceptions=[AnyHTTPStatusError(500, "sess-1")],
        )
        adapter = make_adapter(fake_client)

        await adapter.on_started("OpenCode Agent", "A coding agent")
        with pytest.raises(httpx.HTTPStatusError):
            await deliver_message(
                adapter,
                make_platform_message(content="first try"),
                tools_protocol(tools),
                OpencodeSessionState(),
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )

        await deliver_message(
            adapter,
            make_platform_message(content="second try"),
            tools_protocol(tools),
            OpencodeSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert [call["session_id"] for call in fake_client.prompt_calls] == [
            "sess-1",
            "sess-2",
        ]
        assert not any(
            "still processing the previous request" in event["content"].lower()
            for event in tools.events_sent
        )
        assert any(
            message["content"] == "Recovered after failure"
            for message in tools.messages_sent
        )

    async def test_http_error_reports_status_code_as_failure_code(
        self, make_adapter, tools
    ) -> None:
        """An HTTP error talking to the OpenCode server preserves its status code
        as the failure's ``code``, so a caller can branch on it without parsing
        the message text."""
        fake_client = FakeOpencodeClient(
            prompt_exceptions=[AnyHTTPStatusError(503, "sess-1")]
        )
        adapter = make_adapter(fake_client)

        with pytest.raises(httpx.HTTPStatusError):
            await run_single_turn(adapter, tools)

        failures = reported_failures(tools)
        assert failures
        assert failures[0]["provider"] == "opencode"
        assert failures[0]["code"] == "503"
