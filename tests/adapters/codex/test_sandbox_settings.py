"""Codex adapter: sandbox aliases and policies reach the thread and turn."""

from __future__ import annotations

import pytest

from band.adapters.codex import (
    CodexAdapterConfig,
)
from band.integrations.codex.types import (
    CodexRequestMethod,
)
from tests.adapters.codex.kit import (
    ROOM_ID,
    ToolSchemaFakeTools,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    make_codex_adapter,
    turn_completed,
)


class TestSandboxSettings:
    @pytest.mark.asyncio
    async def test_sandbox_alias_is_normalized_for_thread_and_turn(self) -> None:
        events = [turn_completed()]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(sandbox="dangerFullAccess")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        thread_start = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        # thread/start only accepts the sandbox field (SandboxMode enum)
        assert thread_start["sandbox"] == "danger-full-access"
        # turn/start uses sandboxPolicy (full SandboxPolicy tagged union)
        assert turn_start["sandboxPolicy"]["type"] == "dangerFullAccess"

    @pytest.mark.asyncio
    async def test_external_sandbox_alias_uses_sandbox_policy(self) -> None:
        events = [turn_completed()]
        fake_client = FakeCodexClient(events=events)
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(sandbox="external-sandbox")
        )
        tools = ToolSchemaFakeTools()

        await adapter.on_started("Codex Agent", "A coding agent")
        await send_bootstrap(adapter, tools=tools)

        thread_start = fake_client.params_of(CodexRequestMethod.THREAD_START)[0]
        turn_start = fake_client.params_of(CodexRequestMethod.TURN_START)[0]
        # thread/start has no sandboxPolicy field; externalSandbox is
        # only representable at turn level
        assert "sandbox" not in thread_start
        assert "sandboxPolicy" not in thread_start
        # turn/start can express the full SandboxPolicy tagged union
        assert turn_start["sandboxPolicy"]["type"] == "externalSandbox"

    @pytest.mark.asyncio
    async def test_sandbox_command_blocked_when_sandbox_policy_set(self) -> None:
        """/sandbox is rejected when sandbox_policy is configured."""
        fake_client = FakeCodexClient()
        adapter = make_codex_adapter(
            fake_client, config=CodexAdapterConfig(sandbox_policy={"type": "readOnly"})
        )
        tools = ToolSchemaFakeTools()
        await adapter.on_started("Agent", "A coding agent")
        await send_bootstrap(adapter, content="/sandbox workspace-write", tools=tools)

        assert ROOM_ID not in adapter._sandbox_overrides
        assert "Cannot override sandbox" in tools.messages_sent[0]["content"]
