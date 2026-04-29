"""Tests for tagged-peer, sequential-chain, and buffered synthesis behavior."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_crewai(monkeypatch: pytest.MonkeyPatch):
    fake = MagicMock()
    fake_flow_module = MagicMock()
    fake_flow_module.Flow = type("Flow", (), {})
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
)
from thenvoi.core.types import PlatformMessage  # noqa: E402
from thenvoi.testing.fake_tools import FakeAgentTools  # noqa: E402


def _msg(
    *,
    id: str = "msg-1",
    content: str = "hi",
    sender_id: str = "user-1",
    sender_type: str = "User",
    sender_name: str = "Pat",
) -> PlatformMessage:
    return PlatformMessage(
        id=id,
        room_id="room-1",
        content=content,
        sender_id=sender_id,
        sender_type=sender_type,
        sender_name=sender_name,
        message_type="text",
        metadata={},
        created_at=datetime.now(timezone.utc),
    )


def _flow(decisions: list[Any]):
    queue = list(decisions)

    class _Flow:
        async def kickoff_async(self, inputs: dict | None = None) -> Any:
            return queue.pop(0) if queue else {"decision": "waiting", "reason": "empty"}

    return _Flow()


# ---------------------------------------------------------------------------
# Tagged-peer policy
# ---------------------------------------------------------------------------


class TestTaggedPeer:
    @pytest.mark.asyncio
    async def test_tagged_peer_blocks_finalization(self) -> None:
        # Parent message tags @example/peer-a but flow attempts to synthesize
        # without delegating to peer-a.
        flow = _flow(
            [
                {
                    "decision": "synthesize",
                    "content": "premature final",
                    "mentions": [],
                }
            ]
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(
            participants=[{"id": "p-a", "handle": "@example/peer-a"}]
        )
        await adapter.on_started("router", "")
        await adapter.on_message(
            msg=_msg(content="please ask @example/peer-a about it"),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        # No visible message because the synthesis is blocked.
        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        # Synthesis got buffered (waiting status) instead of finalizing.
        assert "waiting" in statuses
        assert "finalized" not in statuses


# ---------------------------------------------------------------------------
# Sequential chains
# ---------------------------------------------------------------------------


class TestSequentialChains:
    @pytest.mark.asyncio
    async def test_upstream_reply_blocks_until_downstream_delegated(self) -> None:
        ns = "crewai_flow:router"
        # State: upstream peer-a has replied, downstream peer-b has no
        # delegation yet.
        payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "reply_recorded",
            "stage": "waiting_for_replies",
            "join_policy": "first",
            "delegations": [
                {
                    "delegation_id": "d-A",
                    "target": {
                        "participant_id": "p-a",
                        "handle": "@example/peer-a",
                        "normalized_key": "peer-a",
                    },
                    "status": "replied",
                    "side_effect_key": "msg-parent:delegate:d-A",
                    "delegation_message_id": "msg-deleg-A",
                    "reply_message_id": "msg-reply-A",
                }
            ],
        }
        flow = _flow(
            [
                {
                    "decision": "synthesize",
                    "content": "answer",
                    "mentions": [],
                }
            ]
        )
        # Note: we drive the same parent run by sending a synthetic
        # follow-up turn whose msg.id == 'msg-parent'.
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=RestCrewAIFlowStateSource(),
            join_policy="first",
            sequential_chains={"peer-a": "peer-b"},
        )
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a"},
                {"id": "p-b", "handle": "@example/peer-b"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: payload},
                }
            ],
        )
        await adapter.on_started("router", "")
        await adapter.on_message(
            msg=_msg(id="msg-parent", content="orig"),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        # Synthesis blocked because downstream peer-b has no delegation.
        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "finalized" not in statuses

    @pytest.mark.asyncio
    async def test_chain_keys_accept_handle_form(self) -> None:
        ns = "crewai_flow:router"
        payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "reply_recorded",
            "stage": "waiting_for_replies",
            "join_policy": "first",
            "delegations": [
                {
                    "delegation_id": "d-A",
                    "target": {
                        "participant_id": "p-a",
                        "handle": "@example/peer-a",
                        "normalized_key": "peer-a",
                    },
                    "status": "replied",
                    "side_effect_key": "msg-parent:delegate:d-A",
                    "delegation_message_id": "msg-deleg-A",
                    "reply_message_id": "msg-reply-A",
                }
            ],
        }
        flow = _flow(
            [
                {
                    "decision": "synthesize",
                    "content": "answer",
                    "mentions": [],
                }
            ]
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=RestCrewAIFlowStateSource(),
            join_policy="first",
            sequential_chains={"@example/peer-a": "@example/peer-b"},
        )
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a"},
                {"id": "p-b", "handle": "@example/peer-b"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: payload},
                }
            ],
        )
        await adapter.on_started("router", "")
        await adapter.on_message(
            msg=_msg(id="msg-parent", content="orig"),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "waiting" in statuses
        assert "finalized" not in statuses


# ---------------------------------------------------------------------------
# Buffered syntheses
# ---------------------------------------------------------------------------


class TestBufferedSyntheses:
    @pytest.mark.asyncio
    async def test_partial_synth_buffered_when_join_unsatisfied(self) -> None:
        ns = "crewai_flow:router"
        # State has 2 pending delegations, neither replied.
        payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "delegated_pending",
            "stage": "delegated",
            "join_policy": "all",
            "delegations": [
                {
                    "delegation_id": "d-A",
                    "target": {
                        "participant_id": "p-a",
                        "handle": "@example/peer-a",
                        "normalized_key": "peer-a",
                    },
                    "status": "pending",
                    "side_effect_key": "msg-parent:delegate:d-A",
                    "delegation_message_id": "msg-deleg-A",
                },
                {
                    "delegation_id": "d-B",
                    "target": {
                        "participant_id": "p-b",
                        "handle": "@example/peer-b",
                        "normalized_key": "peer-b",
                    },
                    "status": "pending",
                    "side_effect_key": "msg-parent:delegate:d-B",
                    "delegation_message_id": "msg-deleg-B",
                },
            ],
        }
        flow = _flow(
            [
                {
                    "decision": "synthesize",
                    "content": "partial answer 1",
                    "mentions": [],
                }
            ]
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=RestCrewAIFlowStateSource(),
        )
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a"},
                {"id": "p-b", "handle": "@example/peer-b"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: payload},
                }
            ],
        )
        await adapter.on_started("router", "")
        # Drive with the same parent_message_id to operate on that run.
        await adapter.on_message(
            msg=_msg(id="msg-parent", content="orig"),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        assert tools.messages_sent == []
        # A buffered task event was written.
        buffered_events = [
            e
            for e in tools.events_sent
            if e["message_type"] == "task"
            and e["metadata"].get(ns, {}).get("buffered_syntheses")
        ]
        assert len(buffered_events) == 1


# ---------------------------------------------------------------------------
# End-to-end ordered trace
# ---------------------------------------------------------------------------


class TestE2ETrace:
    @pytest.mark.asyncio
    async def test_full_multi_turn_trace_matches_fixture(self) -> None:
        ns = "crewai_flow:router"

        class TraceFlow:
            async def kickoff_async(self, inputs: dict | None = None) -> Any:
                message = (inputs or {}).get("message") or {}
                state = (inputs or {}).get("state") or {}
                runs = state.get("runs") or {}
                active = next(
                    (
                        run
                        for run in runs.values()
                        if run.get("status")
                        not in {"finalized", "failed", "indeterminate"}
                    ),
                    None,
                )
                if message.get("id") == "msg-duplicate":
                    return {"decision": "waiting", "reason": "duplicate starts new run"}
                if active is None:
                    return {
                        "decision": "delegate",
                        "delegations": [
                            {
                                "delegation_id": "d-A",
                                "target": "peer-a",
                                "content": "ask peer A",
                                "mentions": ["@example/peer-a"],
                            },
                            {
                                "delegation_id": "d-B",
                                "target": "peer-b",
                                "content": "ask peer B",
                                "mentions": ["@example/peer-b"],
                            },
                        ],
                    }
                return {
                    "decision": "synthesize",
                    "content": f"partial from {message.get('id')}",
                    "mentions": [],
                }

        def append_task_events_to_context(tools: FakeAgentTools, start: int) -> int:
            for event in tools.events_sent[start:]:
                if event["message_type"] != "task":
                    continue
                tools.append_room_context(
                    {
                        "id": event["id"],
                        "message_type": "task",
                        "inserted_at": datetime.now(timezone.utc).isoformat(),
                        "metadata": event["metadata"],
                    }
                )
            return len(tools.events_sent)

        adapter = CrewAIFlowAdapter(
            flow_factory=TraceFlow,
            state_source=RestCrewAIFlowStateSource(),
            join_policy="all",
        )
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-b", "handle": "@example/peer-b", "name": "Peer B"},
            ]
        )
        await adapter.on_started("router", "")

        event_cursor = 0
        await adapter.on_message(
            msg=_msg(id="msg-parent", content="start"),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=True,
            room_id="room-1",
        )
        event_cursor = append_task_events_to_context(tools, event_cursor)

        await adapter.on_message(
            msg=_msg(
                id="msg-reply-A",
                content="A reply",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        event_cursor = append_task_events_to_context(tools, event_cursor)

        for idx in range(3):
            await adapter.on_message(
                msg=_msg(
                    id=f"msg-chatter-{idx}",
                    content="unrelated",
                    sender_id="@example/noise",
                    sender_type="Agent",
                    sender_name="Noise",
                ),
                tools=tools,  # type: ignore[arg-type]
                history=None,  # type: ignore[arg-type]
                participants_msg=None,
                contacts_msg=None,
                is_session_bootstrap=False,
                room_id="room-1",
            )
        event_cursor = append_task_events_to_context(tools, event_cursor)

        await adapter.on_message(
            msg=_msg(
                id="msg-reply-B",
                content="B reply",
                sender_id="@example/peer-b",
                sender_type="Agent",
                sender_name="Peer B",
            ),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        event_cursor = append_task_events_to_context(tools, event_cursor)

        await adapter.on_message(
            msg=_msg(id="msg-duplicate", content="start"),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        event_cursor = append_task_events_to_context(tools, event_cursor)

        await adapter.on_message(
            msg=_msg(
                id="msg-late-A",
                content="late A reply",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            tools=tools,  # type: ignore[arg-type]
            history=None,  # type: ignore[arg-type]
            participants_msg=None,
            contacts_msg=None,
            is_session_bootstrap=False,
            room_id="room-1",
        )
        append_task_events_to_context(tools, event_cursor)

        fixture_path = (
            Path(__file__).parents[1] / "fixtures" / "crewai_flow_e2e_trace.json"
        )
        expected = json.loads(fixture_path.read_text())
        actual = {
            "visible_messages": [m["content"] for m in tools.messages_sent],
            "task_statuses": [
                e["metadata"].get(ns, {}).get("status")
                for e in tools.events_sent
                if e["message_type"] == "task"
            ],
        }
        assert actual == expected
        assert tools.messages_sent[-1]["mentions"] == ["@example/peer-b"]


# ---------------------------------------------------------------------------
# Identity normalization across forms
# ---------------------------------------------------------------------------


class TestIdentityNormalization:
    def test_uuid_handle_displayname_resolve_to_same_key(self) -> None:
        from thenvoi.converters.crewai_flow import normalize_participant_key

        participants = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "handle": "@example/peer-a",
                "name": "Peer A",
            }
        ]
        results = {
            normalize_participant_key("peer-a", participants=participants),
            normalize_participant_key(
                "11111111-1111-1111-1111-111111111111", participants=participants
            ),
            normalize_participant_key("@example/peer-a", participants=participants),
            normalize_participant_key("Peer A", participants=participants),
        }
        assert results == {"peer-a"}
