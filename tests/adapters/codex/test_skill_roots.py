"""Codex adapter: skill roots."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from band.adapters.codex import (
    CodexAdapter,
    CodexAdapterConfig,
)
from band.core.protocols import TurnResultAlreadyReported
from band.integrations.codex import CodexJsonRpcError
from band.integrations.codex.types import CodexRequestMethod
from tests.adapters.codex.kit import (
    REGISTER_SKILL_ROOT,
    SKILL_ROOT,
    patch_codex_clients_by_room,
    run_codex_turn,
    send_bootstrap,
)
from tests.adapters.codexturns import (
    FakeCodexClient,
    event_notification,
    make_codex_adapter,
    turn_completed,
)


class TestSkillRoots:
    @pytest.mark.asyncio
    async def test_roots_are_sent_first_after_initialize(self) -> None:
        turn = await run_codex_turn(
            events=[turn_completed()],
            config=CodexAdapterConfig(skill_roots=[SKILL_ROOT]),
        )
        assert turn.client.requests[0] == REGISTER_SKILL_ROOT

    @pytest.mark.asyncio
    async def test_no_roots_sends_nothing(self) -> None:
        turn = await run_codex_turn(events=[turn_completed()])
        assert (
            CodexRequestMethod.SKILLS_EXTRA_ROOTS_SET not in turn.client.request_methods
        )

    @pytest.mark.asyncio
    async def test_every_room_process_gets_the_roots(self) -> None:
        clients = {
            room_id: FakeCodexClient(events=[turn_completed()])
            for room_id in ("room-1", "room-2")
        }
        adapter = CodexAdapter(config=CodexAdapterConfig(skill_roots=[SKILL_ROOT]))
        patch_codex_clients_by_room(adapter, clients)
        await adapter.on_started("Codex Agent", "A coding agent")

        for room_id in clients:
            await send_bootstrap(adapter, room_id=room_id)

        assert all(
            client.requests[0] == REGISTER_SKILL_ROOT for client in clients.values()
        )

    @pytest.mark.asyncio
    async def test_a_restarted_process_gets_the_roots_again(self) -> None:
        client = FakeCodexClient(
            events=[
                event_notification("transport/closed", {"reason": "exited"}),
                turn_completed("turn-2"),
            ]
        )
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(skill_roots=[SKILL_ROOT])
        )
        await adapter.on_started("Codex Agent", "A coding agent")

        with pytest.raises(TurnResultAlreadyReported):
            await send_bootstrap(adapter)
        await send_bootstrap(adapter)

        assert client.requests.count(REGISTER_SKILL_ROOT) == 2

    @pytest.mark.asyncio
    async def test_rejected_roots_fail_the_room_start(self) -> None:
        client = FakeCodexClient(
            skill_roots_error=CodexJsonRpcError(code=-32601, message="Method not found")
        )
        adapter = make_codex_adapter(
            client, config=CodexAdapterConfig(skill_roots=[SKILL_ROOT])
        )
        await adapter.on_started("Codex Agent", "A coding agent")

        with pytest.raises(RuntimeError, match="Codex rejected skill_roots"):
            await send_bootstrap(adapter)
        assert client.requests == [REGISTER_SKILL_ROOT]

    def test_relative_roots_are_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be absolute"):
            CodexAdapterConfig(skill_roots=["relative/skills"])

    def test_roots_are_read_from_the_environment_as_json(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CODEX_SKILL_ROOTS", json.dumps([SKILL_ROOT]))
        assert CodexAdapterConfig().skill_roots == [SKILL_ROOT]
