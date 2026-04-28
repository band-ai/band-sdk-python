"""Tests for ``CrewAIFlowAdapter`` constructor and lifecycle."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


# Mock crewai imports BEFORE importing the adapter module so the optional
# dependency guard doesn't pin us to a real install.
@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch: pytest.MonkeyPatch):
    fake_module = MagicMock()
    fake_flow_module = MagicMock()
    fake_flow_module.Flow = type("Flow", (), {})
    fake_flow_module.start = MagicMock()
    fake_flow_module.listen = MagicMock()
    fake_flow_module.router = MagicMock()
    fake_flow_module.and_ = MagicMock()
    fake_flow_module.or_ = MagicMock()
    fake_module.flow = MagicMock()
    fake_module.flow.flow = fake_flow_module

    monkeypatch.setitem(sys.modules, "crewai", fake_module)
    monkeypatch.setitem(sys.modules, "crewai.flow", fake_module.flow)
    monkeypatch.setitem(sys.modules, "crewai.flow.flow", fake_flow_module)
    yield


from thenvoi.adapters.crewai_flow import (  # noqa: E402
    CrewAIFlowAdapter,
    HistoryCrewAIFlowStateSource,
    RestCrewAIFlowStateSource,
)
from thenvoi.core.exceptions import ThenvoiConfigError  # noqa: E402
from thenvoi.core.types import PlatformMessage  # noqa: E402
from thenvoi.testing.fake_tools import FakeAgentTools  # noqa: E402


def _factory():
    return MagicMock()


def _msg(*, id: str = "msg-1", content: str = "hi") -> PlatformMessage:
    return PlatformMessage(
        id=id,
        room_id="room-1",
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Pat",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Constructor defaults and validation
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_defaults(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=_factory)
        assert isinstance(adapter._state_source, RestCrewAIFlowStateSource)
        assert adapter._max_delegation_rounds == 4
        assert adapter._max_run_age == timedelta(days=7)
        assert adapter._accept_agent_initiated is False
        assert adapter.metadata_namespace == ""

    def test_init_does_not_call_flow_factory(self) -> None:
        called = []

        def factory():
            called.append(1)
            return MagicMock()

        CrewAIFlowAdapter(flow_factory=factory)
        assert called == []

    def test_init_accepts_history_state_source(self) -> None:
        adapter = CrewAIFlowAdapter(
            flow_factory=_factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        assert isinstance(adapter._state_source, HistoryCrewAIFlowStateSource)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_flow_factory_must_be_callable(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory="not callable")  # type: ignore[arg-type]

    def test_state_source_must_have_load_task_events(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                state_source=object(),  # type: ignore[arg-type]
            )

    def test_state_source_load_task_events_must_be_awaitable(self) -> None:
        class SyncSource:
            def load_task_events(self, *, room_id, metadata_namespace, tools, history):
                return []

        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                state_source=SyncSource(),  # type: ignore[arg-type]
            )

    def test_join_policy_validation(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                join_policy="some_other",  # type: ignore[arg-type]
            )

    def test_metadata_namespace_must_be_non_empty(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory=_factory, metadata_namespace="")

    def test_max_delegation_rounds_bounds(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory=_factory, max_delegation_rounds=0)
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory=_factory, max_delegation_rounds=21)

    def test_max_run_age_must_be_positive_timedelta(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory=_factory, max_run_age=timedelta(0))
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory=_factory, max_run_age=timedelta(seconds=-1))
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(flow_factory=_factory, max_run_age=86400)  # type: ignore[arg-type]

    def test_text_only_behavior_validation(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                text_only_behavior="bogus",  # type: ignore[arg-type]
            )

    def test_tagged_peer_policy_validation(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                tagged_peer_policy="something_else",  # type: ignore[arg-type]
            )

    def test_sequential_chains_must_be_str_to_str(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                sequential_chains={"k": 123},  # type: ignore[dict-item]
            )

    def test_accept_agent_initiated_must_be_bool(self) -> None:
        with pytest.raises(ThenvoiConfigError):
            CrewAIFlowAdapter(
                flow_factory=_factory,
                accept_agent_initiated="yes",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# on_started / namespace resolution
# ---------------------------------------------------------------------------


class TestFlowInputs:
    @pytest.mark.asyncio
    async def test_inputs_include_current_message_and_requester(self) -> None:
        captured: dict[str, Any] = {}

        class CapturingFlow:
            async def kickoff_async(self, inputs: dict | None = None) -> dict:
                captured.update(inputs or {})
                return {"decision": "waiting", "reason": "captured"}

        adapter = CrewAIFlowAdapter(
            flow_factory=CapturingFlow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("router", "")
        tools = FakeAgentTools()
        msg = _msg(id="msg-request", content="build my brief")
        await adapter.on_message(
            msg=msg,
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert captured["message"] == captured["current_message"]
        assert captured["current_message"] == {
            "id": "msg-request",
            "content": "build my brief",
            "sender_id": "user-1",
            "sender_name": "Pat",
            "sender_type": "User",
            "message_type": "text",
            "created_at": msg.created_at.isoformat(),
        }
        assert captured["requester"] == {
            "id": "user-1",
            "name": "Pat",
            "type": "User",
        }


class TestOnStartedAndNamespace:
    @pytest.mark.asyncio
    async def test_default_namespace_resolves_with_agent_id(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=_factory)
        adapter._thenvoi_agent_id = "agent-id-A"
        await adapter.on_started("Router", "desc")
        assert adapter.metadata_namespace == "crewai_flow:agent-id-A"

    @pytest.mark.asyncio
    async def test_two_adapters_get_distinct_namespaces(self) -> None:
        a = CrewAIFlowAdapter(flow_factory=_factory)
        b = CrewAIFlowAdapter(flow_factory=_factory)
        a._thenvoi_agent_id = "agent-id-X"
        b._thenvoi_agent_id = "agent-id-Y"
        await a.on_started("Router", "")
        await b.on_started("Router", "")
        assert a.metadata_namespace != b.metadata_namespace

    @pytest.mark.asyncio
    async def test_explicit_namespace_overrides_default(self) -> None:
        adapter = CrewAIFlowAdapter(
            flow_factory=_factory, metadata_namespace="custom_ns"
        )
        await adapter.on_started("agent-A", "")
        assert adapter.metadata_namespace == "custom_ns"

    @pytest.mark.asyncio
    async def test_on_started_does_not_call_flow_factory(self) -> None:
        called = []

        def factory():
            called.append(1)
            return MagicMock()

        adapter = CrewAIFlowAdapter(flow_factory=factory)
        await adapter.on_started("agent-A", "")
        assert called == []


# ---------------------------------------------------------------------------
# on_cleanup
# ---------------------------------------------------------------------------


class TestOnCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_clears_state_source_room_cache(self) -> None:
        cleared = []

        class CacheAwareSource(HistoryCrewAIFlowStateSource):
            def clear_room(self, room_id: str, metadata_namespace: str) -> None:
                cleared.append((room_id, metadata_namespace))

        adapter = CrewAIFlowAdapter(
            flow_factory=_factory,
            state_source=CacheAwareSource(acknowledge_test_only=True),
            metadata_namespace="crewai_flow:test-agent",
        )
        await adapter.on_cleanup("room-A")

        assert cleared == [("room-A", "crewai_flow:test-agent")]

    @pytest.mark.asyncio
    async def test_cleanup_removes_only_target_room(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=_factory)
        # Prime locks for two rooms.
        room_a = await adapter._acquire_room_lock_entry("room-A")
        await adapter._release_room_lock_entry("room-A", room_a)
        room_b = await adapter._acquire_room_lock_entry("room-B")
        await adapter._release_room_lock_entry("room-B", room_b)
        assert "room-A" in adapter._room_locks
        assert "room-B" in adapter._room_locks

        await adapter.on_cleanup("room-A")
        assert "room-A" not in adapter._room_locks
        assert "room-B" in adapter._room_locks

    @pytest.mark.asyncio
    async def test_cleanup_waits_for_in_flight_room_turn(self) -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()
        calls: list[str] = []

        class BlockingFlow:
            async def kickoff_async(self, inputs: dict | None = None) -> dict:
                calls.append((inputs or {}).get("message", {}).get("id", ""))
                entered.set()
                if len(calls) == 1:
                    await release.wait()
                finished.set()
                return {"decision": "waiting", "reason": "done"}

        adapter = CrewAIFlowAdapter(
            flow_factory=BlockingFlow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("router", "")
        tools = FakeAgentTools()

        first = asyncio.create_task(
            adapter.on_message(
                msg=_msg(id="msg-1"),
                tools=tools,  # type: ignore[arg-type]
                history=None,  # type: ignore[arg-type]
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await entered.wait()
        cleanup = asyncio.create_task(adapter.on_cleanup("room-1"))
        second = asyncio.create_task(
            adapter.on_message(
                msg=_msg(id="msg-2"),
                tools=tools,  # type: ignore[arg-type]
                history=None,  # type: ignore[arg-type]
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        await asyncio.sleep(0)
        assert calls == ["msg-1"]
        release.set()
        await first
        await cleanup
        await second

        assert calls == ["msg-1", "msg-2"]
        assert finished.is_set()
        assert "room-1" not in adapter._room_locks

    @pytest.mark.asyncio
    async def test_cleanup_does_not_split_lock_for_queued_turns(self) -> None:
        release_first = asyncio.Event()
        release_second = asyncio.Event()
        second_entered = asyncio.Event()
        calls: list[str] = []
        active = 0
        overlap = False

        class BlockingFlow:
            async def kickoff_async(self, inputs: dict | None = None) -> dict:
                nonlocal active, overlap
                message_id = (inputs or {}).get("message", {}).get("id", "")
                if active:
                    overlap = True
                active += 1
                calls.append(message_id)
                try:
                    if message_id == "msg-1":
                        await release_first.wait()
                    elif message_id == "msg-2":
                        second_entered.set()
                        await release_second.wait()
                    return {"decision": "waiting", "reason": message_id}
                finally:
                    active -= 1

        adapter = CrewAIFlowAdapter(
            flow_factory=BlockingFlow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("router", "")
        tools = FakeAgentTools()

        first = asyncio.create_task(
            adapter.on_message(
                msg=_msg(id="msg-1"),
                tools=tools,  # type: ignore[arg-type]
                history=None,  # type: ignore[arg-type]
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await asyncio.sleep(0)
        cleanup = asyncio.create_task(adapter.on_cleanup("room-1"))
        second = asyncio.create_task(
            adapter.on_message(
                msg=_msg(id="msg-2"),
                tools=tools,  # type: ignore[arg-type]
                history=None,  # type: ignore[arg-type]
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )

        release_first.set()
        await first
        await cleanup
        await second_entered.wait()
        third = asyncio.create_task(
            adapter.on_message(
                msg=_msg(id="msg-3"),
                tools=tools,  # type: ignore[arg-type]
                history=None,  # type: ignore[arg-type]
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=True,
                room_id="room-1",
            )
        )
        await asyncio.sleep(0)
        assert calls == ["msg-1", "msg-2"]

        release_second.set()
        await second
        await third

        assert calls == ["msg-1", "msg-2", "msg-3"]
        assert not overlap
        assert "room-1" not in adapter._room_locks


# ---------------------------------------------------------------------------
# Public import path matches the example
# ---------------------------------------------------------------------------


class TestPublicImportPath:
    def test_lazy_import_from_thenvoi_adapters(self) -> None:
        # The example imports `from thenvoi.adapters import CrewAIFlowAdapter`.
        from thenvoi.adapters import CrewAIFlowAdapter as Imported

        assert Imported is CrewAIFlowAdapter

    @pytest.mark.asyncio
    async def test_default_namespace_matches_documented_format(self) -> None:
        adapter = CrewAIFlowAdapter(flow_factory=_factory)
        adapter._thenvoi_agent_id = "crewai-flow-router-id"
        await adapter.on_started("crewai_flow_router", "")
        assert adapter.metadata_namespace == "crewai_flow:crewai-flow-router-id"
