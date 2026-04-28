"""Tests for CrewAIFlowAdapter message processing."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch: pytest.MonkeyPatch):
    fake = MagicMock()
    fake_flow_module = MagicMock()

    class _FakeFlow:
        pass

    fake_flow_module.Flow = _FakeFlow
    fake_flow_module.start = MagicMock()
    fake_flow_module.listen = MagicMock()
    fake_flow_module.router = MagicMock()
    fake_flow_module.and_ = MagicMock()
    fake_flow_module.or_ = MagicMock()
    fake.flow = MagicMock()
    fake.flow.flow = fake_flow_module
    monkeypatch.setitem(sys.modules, "crewai", fake)
    monkeypatch.setitem(sys.modules, "crewai.flow", fake.flow)
    monkeypatch.setitem(sys.modules, "crewai.flow.flow", fake_flow_module)
    yield


from thenvoi.adapters.crewai_flow import (  # noqa: E402
    CrewAIFlowAdapter,
    HistoryCrewAIFlowStateSource,
    RestCrewAIFlowStateSource,
    get_current_flow_runtime,
)
from thenvoi.converters.crewai_flow import CrewAIFlowStateConverter  # noqa: E402
from thenvoi.core.types import AdapterFeatures, Capability, PlatformMessage  # noqa: E402
from thenvoi.testing.fake_tools import FakeAgentTools  # noqa: E402


def _msg(idx: int = 1, content: str = "hi") -> PlatformMessage:
    return PlatformMessage(
        id=f"msg-{idx}",
        room_id="room-1",
        content=content,
        sender_id="user-1",
        sender_type="User",
        sender_name="Pat",
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _make_flow_returning(decision: Any):
    """Build a fake Flow whose kickoff_async returns ``decision``."""

    class _Flow:
        async def kickoff_async(self, inputs: dict | None = None) -> Any:
            return decision

    return _Flow()


async def _run_one_turn(
    adapter: CrewAIFlowAdapter, tools: FakeAgentTools, msg: PlatformMessage
) -> None:
    await adapter.on_started("agent-1", "")
    await adapter.on_message(
        msg=msg,
        tools=tools,  # type: ignore[arg-type]
        history=None,  # type: ignore[arg-type]
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=True,
        room_id="room-1",
    )


# ---------------------------------------------------------------------------
# direct_response
# ---------------------------------------------------------------------------


class TestDirectResponse:
    @pytest.mark.asyncio
    async def test_direct_response_sends_one_visible_and_records_finalized(
        self,
    ) -> None:
        flow = _make_flow_returning(
            {
                "decision": "direct_response",
                "content": "hello",
                "mentions": ["@example/peer"],
            }
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(participants=[{"id": "p1", "handle": "@example/peer"}])
        await _run_one_turn(adapter, tools, _msg())

        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "hello"
        assert tools.messages_sent[0]["mentions"] == ["@example/peer"]
        # At least: reservation event + finalized event.
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e.get("message_type") == "task"
        ]
        assert "side_effect_reserved" in statuses
        assert "finalized" in statuses


# ---------------------------------------------------------------------------
# waiting
# ---------------------------------------------------------------------------


class TestWaiting:
    @pytest.mark.asyncio
    async def test_waiting_emits_no_visible_message(self) -> None:
        flow = _make_flow_returning(
            {"decision": "waiting", "reason": "peer hasn't replied yet"}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e.get("message_type") == "task"
        ]
        assert "waiting" in statuses


# ---------------------------------------------------------------------------
# failed / malformed / streaming
# ---------------------------------------------------------------------------


class TestFailedAndMalformed:
    @pytest.mark.asyncio
    async def test_failed_decision_emits_error_and_failed_task(self) -> None:
        flow = _make_flow_returning(
            {
                "decision": "failed",
                "error": {"code": "my_code", "message": "explanation"},
            }
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        error_events = [e for e in tools.events_sent if e["message_type"] == "error"]
        task_events = [e for e in tools.events_sent if e["message_type"] == "task"]
        assert len(error_events) == 1
        assert any(
            e["metadata"].get(adapter.metadata_namespace, {}).get("status") == "failed"
            for e in task_events
        )
        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_malformed_output_records_failed(self) -> None:
        flow = _make_flow_returning({"foo": "bar"})  # no decision field
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "failed" in statuses
        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_text_only_fallback_fails_when_delegation_pending(self) -> None:
        payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-1",
            "parent_message_id": "msg-1",
            "status": "delegated_pending",
            "stage": "delegated",
            "delegations": [
                {
                    "delegation_id": "d-A",
                    "target": {
                        "participant_id": "p-a",
                        "handle": "@example/peer-a",
                        "normalized_key": "peer-a",
                    },
                    "status": "pending",
                    "side_effect_key": "msg-1:delegate:d-A",
                    "delegation_message_id": "msg-deleg-A",
                }
            ],
        }
        converted_history = CrewAIFlowStateConverter().convert(
            [
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": "2026-01-01T00:00:00+00:00",
                    "metadata": {"crewai_flow": payload},
                }
            ]
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _make_flow_returning("partial answer"),
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
            text_only_behavior="fallback_send",
        )
        await adapter.on_started("agent-1", "")
        tools = FakeAgentTools()
        await adapter.on_message(
            msg=_msg(),
            tools=tools,  # type: ignore[arg-type]
            history=converted_history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "failed" in statuses

    @pytest.mark.asyncio
    async def test_streaming_output_rejected(self) -> None:
        # Simulate the FlowStreamingOutput class by name-match.
        class FlowStreamingOutput:
            pass

        flow = _make_flow_returning(FlowStreamingOutput())
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "failed" in statuses
        assert tools.messages_sent == []


# ---------------------------------------------------------------------------
# flow_factory exception
# ---------------------------------------------------------------------------


class TestFlowFactoryException:
    @pytest.mark.asyncio
    async def test_flow_factory_raises_records_failed(self) -> None:
        def factory():
            raise RuntimeError("eager init failed")

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        # Must not propagate.
        await _run_one_turn(adapter, tools, _msg())

        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        codes = [
            e["metadata"]
            .get(adapter.metadata_namespace, {})
            .get("error", {})
            .get("code")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "failed" in statuses
        assert "flow_factory_error" in codes
        assert tools.messages_sent == []


# ---------------------------------------------------------------------------
# nest_asyncio not invoked
# ---------------------------------------------------------------------------


class TestNestAsyncioNotInvoked:
    @pytest.mark.asyncio
    async def test_direct_response_does_not_apply_nest_asyncio(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Patch nest_asyncio.apply at the module level.
        try:
            import nest_asyncio  # type: ignore

            apply_mock = MagicMock()
            monkeypatch.setattr(nest_asyncio, "apply", apply_mock)
        except ImportError:
            pytest.skip("nest_asyncio not installed")

        flow = _make_flow_returning(
            {"decision": "direct_response", "content": "hi", "mentions": []}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(participants=[{"id": "user-1", "handle": "@pat"}])
        await _run_one_turn(adapter, tools, _msg())

        apply_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_direct_response_without_mentions_targets_sender(self) -> None:
        flow = _make_flow_returning(
            {"decision": "direct_response", "content": "hi", "mentions": []}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(participants=[{"id": "user-1", "handle": "@pat"}])

        await _run_one_turn(adapter, tools, _msg())

        assert tools.messages_sent[0]["mentions"] == ["@pat"]


# ---------------------------------------------------------------------------
# Idempotency (already-finalized state)
# ---------------------------------------------------------------------------


class TestIdempotentFinalization:
    @pytest.mark.asyncio
    async def test_finalized_state_does_not_double_send(self) -> None:

        # Pre-populate the state source with a finalized record for msg-1.
        finalized_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-1",
            "parent_message_id": "msg-1",
            "status": "finalized",
            "stage": "done",
        }

        flow = _make_flow_returning(
            {
                "decision": "direct_response",
                "content": "should NOT send",
                "mentions": [],
            }
        )
        adapter = CrewAIFlowAdapter(flow_factory=lambda: flow)
        await adapter.on_started("agent-1", "")

        ns = adapter.metadata_namespace
        tools = FakeAgentTools(
            participants=[{"id": "p1", "handle": "@example/peer"}],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": "2026-01-01T00:00:00+00:00",
                    "metadata": {ns: finalized_payload},
                }
            ],
        )
        await adapter.on_message(
            msg=_msg(),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_history_state_source_accepts_converted_history(self) -> None:
        finalized_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-1",
            "parent_message_id": "msg-1",
            "status": "finalized",
            "stage": "done",
        }
        converted_history = CrewAIFlowStateConverter().convert(
            [
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": "2026-01-01T00:00:00+00:00",
                    "metadata": {"crewai_flow": finalized_payload},
                }
            ]
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _make_flow_returning(
                {
                    "decision": "direct_response",
                    "content": "should NOT send",
                    "mentions": [],
                }
            ),
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await adapter.on_started("agent-1", "")
        tools = FakeAgentTools()
        await adapter.on_message(
            msg=_msg(),
            tools=tools,  # type: ignore[arg-type]
            history=converted_history,
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        assert tools.messages_sent == []


# ---------------------------------------------------------------------------
# Runtime tools: read-only surface
# ---------------------------------------------------------------------------


class TestRuntimeTools:
    @pytest.mark.asyncio
    async def test_runtime_tools_does_not_expose_writers(self) -> None:
        captured = {}

        def factory():
            class _Flow:
                async def kickoff_async(self, inputs: dict | None = None) -> Any:
                    rt = get_current_flow_runtime()
                    captured["runtime"] = rt
                    return {"decision": "waiting", "reason": "checking surface"}

            return _Flow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        rt = captured["runtime"]
        assert rt is not None
        public_attrs = {n for n in dir(rt) if not n.startswith("_")}
        for forbidden in (
            "send_message",
            "send_event",
            "add_participant",
            "remove_participant",
        ):
            assert forbidden not in public_attrs
        assert "ensure_participant" in public_attrs
        assert "create_crewai_tools" in public_attrs

    @pytest.mark.asyncio
    async def test_ensure_participant_refreshes_snapshot_for_same_turn_delegation(
        self,
    ) -> None:
        def factory():
            class _Flow:
                async def kickoff_async(self, inputs: dict | None = None) -> Any:
                    rt = get_current_flow_runtime()
                    assert rt is not None
                    participant = await rt.ensure_participant("@example/peer")
                    assert participant.normalized_key == "peer"
                    return {
                        "decision": "delegate",
                        "delegations": [
                            {
                                "delegation_id": "d-peer",
                                "target": "peer",
                                "content": "please help",
                                "mentions": ["@example/peer"],
                            }
                        ],
                    }

            return _Flow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        assert tools.participants_added == [
            {
                "id": "p-@example/peer",
                "name": "@example/peer",
                "role": "member",
                "handle": "@example/peer",
            }
        ]
        assert tools.messages_sent == [
            {"id": "msg-0", "content": "please help", "mentions": ["@example/peer"]}
        ]

    @pytest.mark.asyncio
    async def test_create_crewai_tools_applies_adapter_feature_filters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_tools_module = MagicMock()
        mock_tools_module.BaseTool = type("BaseTool", (), {})
        monkeypatch.setitem(sys.modules, "crewai.tools", mock_tools_module)
        for mod in (
            "thenvoi.integrations.crewai",
            "thenvoi.integrations.crewai.runtime",
            "thenvoi.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)

        captured = {}

        def factory():
            class _Flow:
                async def kickoff_async(self, inputs: dict | None = None) -> Any:
                    rt = get_current_flow_runtime()
                    assert rt is not None
                    captured["tool_names"] = {
                        t.name
                        for t in rt.create_crewai_tools(
                            capabilities=frozenset({Capability.CONTACTS})
                        )
                    }
                    return {"decision": "waiting", "reason": "checking tools"}

            return _Flow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
            features=AdapterFeatures(
                include_categories=("contacts",),
                exclude_tools=("thenvoi_remove_contact",),
            ),
        )
        tools = FakeAgentTools()
        await _run_one_turn(adapter, tools, _msg())

        assert "thenvoi_list_contacts" in captured["tool_names"]
        assert "thenvoi_send_message" not in captured["tool_names"]
        assert "thenvoi_remove_contact" not in captured["tool_names"]

    @pytest.mark.asyncio
    async def test_subcrew_send_failure_aborts_parent_finalization(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_tools_module = MagicMock()
        mock_tools_module.BaseTool = type("BaseTool", (), {})
        monkeypatch.setitem(sys.modules, "crewai.tools", mock_tools_module)
        for mod in (
            "thenvoi.integrations.crewai",
            "thenvoi.integrations.crewai.runtime",
            "thenvoi.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)

        class FailingSendTools(FakeAgentTools):
            async def send_message(
                self,
                content: str,
                mentions: list[str] | list[dict[str, str]] | None = None,
            ) -> dict[str, Any]:
                raise RuntimeError("send failed")

        def factory():
            class _Flow:
                async def kickoff_async(self, inputs: dict | None = None) -> Any:
                    rt = get_current_flow_runtime()
                    assert rt is not None
                    send_tool = next(
                        t
                        for t in rt.create_crewai_tools()
                        if t.name == "thenvoi_send_message"
                    )
                    send_tool._run(content="subcrew visible", mentions="[]")
                    return {
                        "decision": "direct_response",
                        "content": "should not finalize",
                        "mentions": [],
                    }

            return _Flow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FailingSendTools()
        await _run_one_turn(adapter, tools, _msg())

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert statuses == ["side_effect_reserved", "indeterminate"]

    @pytest.mark.asyncio
    async def test_subcrew_send_is_not_repeated_after_confirmation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_tools_module = MagicMock()
        mock_tools_module.BaseTool = type("BaseTool", (), {})
        monkeypatch.setitem(sys.modules, "crewai.tools", mock_tools_module)
        for mod in (
            "thenvoi.integrations.crewai",
            "thenvoi.integrations.crewai.runtime",
            "thenvoi.integrations.crewai.tools",
        ):
            sys.modules.pop(mod, None)

        ns = "crewai_flow:agent-1"
        prior_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-1",
            "parent_message_id": "msg-1",
            "status": "waiting",
            "stage": "waiting_for_replies",
            "final_side_effect_key": "msg-1:subcrew:1",
            "final_message_id": "msg-existing",
            "side_effects": [
                {
                    "side_effect_key": "msg-1:subcrew:1",
                    "message_id": "msg-existing",
                }
            ],
        }

        def factory():
            class _Flow:
                async def kickoff_async(self, inputs: dict | None = None) -> Any:
                    rt = get_current_flow_runtime()
                    assert rt is not None
                    send_tool = next(
                        t
                        for t in rt.create_crewai_tools()
                        if t.name == "thenvoi_send_message"
                    )
                    result = send_tool._run(content="subcrew visible", mentions="[]")
                    assert '"status": "success"' in result
                    return {"decision": "waiting", "reason": "done"}

            return _Flow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=RestCrewAIFlowStateSource(),
        )
        await adapter.on_started("agent-1", "")
        tools = FakeAgentTools(
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: prior_payload},
                }
            ]
        )

        await adapter.on_message(
            msg=_msg(),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert tools.messages_sent == []

    @pytest.mark.asyncio
    async def test_subcrew_tools_run_on_adapter_loop_from_worker_thread(self) -> None:
        expected_loop = asyncio.get_running_loop()

        class LoopCheckingTools(FakeAgentTools):
            async def send_message(
                self,
                content: str,
                mentions: list[str] | list[dict[str, str]] | None = None,
            ) -> dict[str, Any]:
                assert asyncio.get_running_loop() is expected_loop
                return await super().send_message(content, mentions)

        def factory():
            class _Flow:
                async def kickoff_async(self, inputs: dict | None = None) -> Any:
                    rt = get_current_flow_runtime()
                    assert rt is not None
                    send_tool = next(
                        t
                        for t in rt.create_crewai_tools()
                        if t.name == "thenvoi_send_message"
                    )
                    result = await asyncio.to_thread(
                        send_tool._run,
                        content="subcrew visible",
                        mentions='["@example/peer"]',
                    )
                    assert '"status": "success"' in result
                    return {"decision": "waiting", "reason": "done"}

            return _Flow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = LoopCheckingTools(
            participants=[{"id": "p-a", "handle": "@example/peer"}]
        )

        await _run_one_turn(adapter, tools, _msg())

        assert tools.messages_sent == [
            {"id": "msg-0", "content": "subcrew visible", "mentions": ["@example/peer"]}
        ]


# ---------------------------------------------------------------------------
# flow_state mutation does not persist
# ---------------------------------------------------------------------------


class TestFlowStateScratch:
    @pytest.mark.asyncio
    async def test_self_state_mutation_is_scratch(self) -> None:
        """A Flow that mutates self.state during a turn — next turn ignores it."""

        class MutatingFlow:
            async def kickoff_async(self, inputs: dict | None = None) -> Any:
                # Mutate scratch state.
                self.state = {"scratch": "value"}
                return {"decision": "waiting", "reason": "test"}

        def factory():
            return MutatingFlow()

        adapter = CrewAIFlowAdapter(
            flow_factory=factory,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()

        await _run_one_turn(adapter, tools, _msg(idx=1))
        await adapter.on_message(
            msg=_msg(idx=2),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        # Both turns should record waiting; nothing leaks via Flow state.
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert statuses.count("waiting") >= 2
