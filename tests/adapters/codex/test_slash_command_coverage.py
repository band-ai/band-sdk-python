"""Codex adapter: slash command coverage."""

from __future__ import annotations

import logging

import pytest

from band.adapters.codex import CodexAdapterConfig
from band.converters.codex import CODEX_RESUME_RECORD
from band.integrations.codex.types import CodexRequestMethod, CodexSessionState
from band.testing import reported_failures
from tests.adapters.codex.kit import (
    NOTICE_REJECTED,
    REPLAYED,
    ROOM_ID,
    TRANSCRIPT,
    FailingNoticeTools,
    ToolSchemaFakeTools,
    agent_input,
    answered_turn,
    make_platform_message,
    replayed_history,
    send_bootstrap,
    stored,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)
from tests.adapters.dispatch import deliver_message


class TestSlashCommandCoverage:
    @pytest.mark.asyncio
    async def test_thread_info_with_no_mapping(self) -> None:
        """/thread info reports gracefully when the room has no thread yet."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/thread info", tools=tools)

        assert tools.messages_sent
        assert "No thread mapped" in tools.messages_sent[-1]["content"]

    @pytest.mark.asyncio
    async def test_thread_info_includes_thread_and_usage(self) -> None:
        """/thread info echoes current thread id and token usage summary."""
        events = [
            event_notification(
                "thread/tokenUsage/updated",
                {
                    "usage": {
                        "inputTokens": 100,
                        "outputTokens": 50,
                        "totalTokens": 150,
                    }
                },
            ),
            turn_completed(),
        ]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(emit_token_usage_events=True)
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)
        await deliver_message(
            adapter,
            make_platform_message(content="/thread info"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id=ROOM_ID,
        )

        info_msgs = [m for m in tools.messages_sent if "Thread info:" in m["content"]]
        assert len(info_msgs) == 1
        content = info_msgs[0]["content"]
        assert "thread_id: thr-1" in content
        assert "room_id: room-1" in content
        assert "150" in content

    @pytest.mark.asyncio
    async def test_permissions_reflects_sandbox_override(self) -> None:
        """/permissions reports the per-room sandbox override once set."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/sandbox read-only", tools=tools)
        await deliver_message(
            adapter,
            make_platform_message(content="/permissions"),
            tools,
            CodexSessionState(),
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id=ROOM_ID,
        )

        perm_msgs = [
            m for m in tools.messages_sent if "Effective permissions:" in m["content"]
        ]
        assert len(perm_msgs) == 1
        assert "read-only" in perm_msgs[0]["content"]

    @pytest.mark.asyncio
    async def test_local_command_reply_delivery_failure_is_not_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """/help's answer failing to post is Band-side delivery, not a Codex
        provider failure -- its DeliveryFailedError must be
        recognized and left unreported here. The original cause still
        propagates (the delivery is still marked FAILED), just never
        misreported as a Codex AgentFailure."""

        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = FailingNoticeTools()

        await adapter.on_started("Agent", "A coding agent")
        with (
            caplog.at_level(logging.ERROR, logger="band.core.delivery"),
            pytest.raises(RuntimeError, match=NOTICE_REJECTED),
        ):
            await send_bootstrap(adapter, content="/help", tools=tools)

        assert not tools.messages_sent
        assert not reported_failures(tools)
        assert any(
            "Reply delivery failed" in record.message for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_approval_command_reply_delivery_failure_is_not_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Same delivery-vs-provider-failure split as slash commands, but for
        the approval-command path, which runs outside on_message's main
        try/except and needs its own DeliveryFailedError handling."""

        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        tools = FailingNoticeTools()

        await adapter.on_started("Agent", "A coding agent")
        with (
            caplog.at_level(logging.ERROR, logger="band.core.delivery"),
            pytest.raises(RuntimeError, match=NOTICE_REJECTED),
        ):
            await send_bootstrap(adapter, content="/approvals", tools=tools)

        assert not tools.messages_sent
        assert not reported_failures(tools)
        assert any(
            "Reply delivery failed" in record.message for record in caplog.records
        )

    @pytest.mark.asyncio
    async def test_thread_archive_starts_a_fresh_thread_seeded_from_the_room(
        self,
    ) -> None:
        """After /thread archive the next turn starts a new thread, replays the
        room's stored transcript into it, and records it in place of the old."""
        fake_client = FakeCodexClient(
            events=[*answered_turn("turn-1"), *answered_turn("turn-2")]
        )
        adapter = make_codex_adapter(fake_client, config=CodexAdapterConfig())
        await adapter.on_started("Agent", "A coding agent")
        tools = ToolSchemaFakeTools()
        seeding = agent_input("hello", tools, raw_history=TRANSCRIPT)
        archive = agent_input("/thread archive", tools, is_session_bootstrap=False)
        following = agent_input("start over", tools, is_session_bootstrap=False)
        tools.set_room_context(
            [*TRANSCRIPT, *(stored(i.msg) for i in (seeding, archive, following))]
        )

        await adapter.on_event(seeding)
        await adapter.on_event(archive)
        await adapter.on_event(following)

        assert fake_client.request_methods.count(CodexRequestMethod.THREAD_START) == 2
        first, second = fake_client.params_of(CodexRequestMethod.TURN_START)
        assert second["threadId"] != first["threadId"]
        (history,) = replayed_history(second)
        assert REPLAYED in history
        assert "[Alice]: hello" in history
        assert CODEX_RESUME_RECORD.candidates(tools.events_sent) == {
            ROOM_ID: second["threadId"]
        }
