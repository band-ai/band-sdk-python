"""Persisted native sessions resume, or fall back to a fresh session and replay."""

from __future__ import annotations

from pathlib import Path

import pytest

from band.adapters.omp import OmpAdapterConfig
from band.converters.omp import OmpSessionState
from band.integrations.ompwire import OmpCommandName
from band.testing import FakeAgentTools
from tests.adapters.omp.custody import owned_room
from tests.adapters.omp.modelpeer import NativeFault
from tests.adapters.omp.runtime import AdapterFactory
from tests.adapters.omp.sessions import native_session_file
from tests.adapters.omp.turns import run_turn
from tests.integrations.omprpc.wirelog import (
    sent_command_parameters,
    sent_prompt_messages,
)


class TestSessionRestore:
    @pytest.mark.asyncio
    async def test_persisted_session_resumes_without_replaying_history(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        saved = native_session_file(tmp_path)
        adapter = real_adapter(
            config=OmpAdapterConfig(),
            workspace_for_room=lambda _room: str(tmp_path),
            emit=(),
        )
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        await run_turn(adapter, FakeAgentTools(), history=history)
        client = owned_room(adapter, "room-123").client
        prompt_text = "\n".join(await sent_prompt_messages(client))

        assert (
            await sent_command_parameters(client, OmpCommandName.SWITCH_SESSION)
        ) == [{"sessionPath": str(saved)}]
        assert (await sent_command_parameters(client, OmpCommandName.NEW_SESSION)) == []
        state = await client.request(OmpCommandName.GET_STATE)
        assert state["sessionFile"] == str(saved)
        assert "Conversation History" not in prompt_text
        assert "[Alice]: earlier" not in prompt_text

    @pytest.mark.asyncio
    async def test_failed_restore_starts_fresh_and_replays_history(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        saved = native_session_file(tmp_path)
        adapter = real_adapter(
            faults=(NativeFault.SWITCH_FAILURE,),
            config=OmpAdapterConfig(),
            workspace_for_room=lambda _room: str(tmp_path),
            emit=(),
        )
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        await run_turn(adapter, FakeAgentTools(), history=history)
        client = owned_room(adapter, "room-123").client
        prompt_text = "\n".join(await sent_prompt_messages(client))

        assert (
            await sent_command_parameters(client, OmpCommandName.SWITCH_SESSION)
        ) == [{"sessionPath": str(saved)}]
        assert (await sent_command_parameters(client, OmpCommandName.NEW_SESSION)) == [
            {}
        ]
        state = await client.request(OmpCommandName.GET_STATE)
        assert state["sessionFile"] is None
        assert "[Alice]: earlier" in prompt_text

    @pytest.mark.asyncio
    async def test_cancelled_switch_replays_without_new_session(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        saved = native_session_file(tmp_path)
        adapter = real_adapter(
            faults=(NativeFault.SWITCH_CANCELLED,),
            config=OmpAdapterConfig(),
            workspace_for_room=lambda _room: str(tmp_path),
            emit=(),
        )
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        await run_turn(adapter, FakeAgentTools(), history=history)
        client = owned_room(adapter, "room-123").client
        prompt_text = "\n".join(await sent_prompt_messages(client))
        assert (await sent_command_parameters(client, OmpCommandName.NEW_SESSION)) == []
        assert "[Alice]: earlier" in prompt_text

    @pytest.mark.asyncio
    async def test_cancelled_new_session_fails_closed(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        saved = native_session_file(tmp_path)
        adapter = real_adapter(
            config=OmpAdapterConfig(),
            faults=(NativeFault.SWITCH_FAILURE, NativeFault.NEW_SESSION_CANCELLED),
            workspace_for_room=lambda _room: str(tmp_path),
            emit=(),
        )
        history = OmpSessionState(
            room_to_session={"room-123": str(saved)},
            replay_messages=["[Alice]: earlier"],
        )
        with pytest.raises(RuntimeError, match="replacement session"):
            await run_turn(adapter, FakeAgentTools(), history=history)

    @pytest.mark.asyncio
    async def test_foreign_session_path_is_not_restored(
        self, tmp_path: Path, real_adapter: AdapterFactory
    ) -> None:
        foreign = tmp_path / "outside.jsonl"
        foreign.write_text("{}", encoding="utf-8")
        adapter = real_adapter(
            config=OmpAdapterConfig(),
            workspace_for_room=lambda _room: str(tmp_path),
            emit=(),
        )
        history = OmpSessionState(
            room_to_session={"room-123": str(foreign)},
            replay_messages=["[Alice]: earlier"],
        )
        await run_turn(adapter, FakeAgentTools(), history=history)
        client = owned_room(adapter, "room-123").client
        prompt_text = "\n".join(await sent_prompt_messages(client))
        assert (
            await sent_command_parameters(client, OmpCommandName.SWITCH_SESSION)
        ) == []
        assert "[Alice]: earlier" in prompt_text
