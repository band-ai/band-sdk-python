"""Tests for delegation, reply matching, and join handling."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
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

NS_PREFIX = "crewai_flow:"


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


def _flow(decision: Any | list[Any]):
    """Build a flow whose kickoff_async returns ``decision`` (or pops list)."""
    queue = decision if isinstance(decision, list) else [decision]

    class _Flow:
        async def kickoff_async(self, inputs: dict | None = None) -> Any:
            return queue.pop(0) if queue else {"decision": "waiting", "reason": "empty"}

    return _Flow()


async def _start(adapter: CrewAIFlowAdapter, name: str = "router") -> None:
    await adapter.on_started(name, "")


async def _turn(
    adapter: CrewAIFlowAdapter,
    tools: FakeAgentTools,
    msg: PlatformMessage,
    *,
    is_session_bootstrap: bool = True,
) -> None:
    await adapter.on_message(
        msg=msg,
        tools=tools,  # type: ignore[arg-type]
        history=None,  # type: ignore[arg-type]
        participants_msg=None,
        contacts_msg=None,
        is_session_bootstrap=is_session_bootstrap,
        room_id="room-1",
    )


# ---------------------------------------------------------------------------
# Delegation
# ---------------------------------------------------------------------------


class TestDelegation:
    @pytest.mark.asyncio
    async def test_duplicate_delegation_ids_fail_before_sending(self) -> None:
        flow = _flow(
            {
                "decision": "delegate",
                "delegations": [
                    {
                        "delegation_id": "dup",
                        "target": "peer-a",
                        "content": "do A",
                        "mentions": ["@example/peer-a"],
                    },
                    {
                        "delegation_id": "dup",
                        "target": "peer-b",
                        "content": "do B",
                        "mentions": ["@example/peer-b"],
                    },
                ],
            }
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        await _start(adapter)
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a"},
                {"id": "p-b", "handle": "@example/peer-b"},
            ]
        )

        await _turn(adapter, tools, _msg())

        assert tools.messages_sent == []
        payloads = [
            e["metadata"].get(adapter.metadata_namespace, {})
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert payloads[-1]["status"] == "failed"
        assert payloads[-1]["error"]["code"] == "malformed_flow_output"
        assert "duplicate delegation_id" in payloads[-1]["error"]["message"]

    @pytest.mark.asyncio
    async def test_two_delegations_send_two_visible_messages(self) -> None:
        flow = _flow(
            {
                "decision": "delegate",
                "delegations": [
                    {
                        "delegation_id": "msg-1:peer-a",
                        "target": "peer-a",
                        "content": "do A",
                        "mentions": ["@example/peer-a"],
                    },
                    {
                        "delegation_id": "msg-1:peer-b",
                        "target": "peer-b",
                        "content": "do B",
                        "mentions": ["@example/peer-b"],
                    },
                ],
            }
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-b", "handle": "@example/peer-b", "name": "Peer B"},
            ]
        )
        await _start(adapter)
        await _turn(adapter, tools, _msg())

        assert len(tools.messages_sent) == 2
        ns = adapter.metadata_namespace
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        # 2 reservations + 2 confirmations.
        assert statuses.count("side_effect_reserved") >= 2
        assert statuses.count("delegated_pending") >= 2


# ---------------------------------------------------------------------------
# Reply matching across turns
# ---------------------------------------------------------------------------


class TestReplyMatching:
    @pytest.mark.asyncio
    async def test_one_reply_under_join_all_does_not_finalize(self) -> None:
        # Pre-populate the platform context with a delegated_pending state.
        ns = "crewai_flow:router"
        delegation_payload = {
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
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-b", "handle": "@example/peer-b", "name": "Peer B"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: delegation_payload},
                }
            ],
        )
        # Flow can run after the reply is recorded, but this decision still waits.
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow({"decision": "waiting", "reason": "no"}),
            state_source=RestCrewAIFlowStateSource(),
        )
        await _start(adapter, "router")
        # Peer A replies.
        peer_reply = _msg(
            id="msg-reply-A",
            content="here is A's answer",
            sender_id="@example/peer-a",
            sender_type="Agent",
            sender_name="Peer A",
        )
        await _turn(adapter, tools, peer_reply, is_session_bootstrap=False)

        # Visible message count unchanged (no synthesis yet).
        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "reply_recorded" in statuses
        assert "finalized" not in statuses

    @pytest.mark.asyncio
    async def test_second_reply_under_join_all_finalizes(self) -> None:
        ns = "crewai_flow:router"
        delegation_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "reply_recorded",
            "stage": "waiting_for_replies",
            "join_policy": "all",
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
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-b", "handle": "@example/peer-b", "name": "Peer B"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: delegation_payload},
                }
            ],
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow(
                {"decision": "synthesize", "content": "final answer", "mentions": []}
            ),
            state_source=RestCrewAIFlowStateSource(),
        )
        await _start(adapter, "router")
        await _turn(
            adapter,
            tools,
            _msg(
                id="msg-reply-B",
                content="here is B's answer",
                sender_id="@example/peer-b",
                sender_type="Agent",
                sender_name="Peer B",
            ),
            is_session_bootstrap=False,
        )

        assert tools.messages_sent == [
            {"id": "msg-0", "content": "final answer", "mentions": ["@example/peer-b"]}
        ]
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "reply_recorded" in statuses
        assert "finalized" in statuses

    @pytest.mark.asyncio
    async def test_first_reply_under_join_first_finalizes(self) -> None:
        ns = "crewai_flow:router"
        delegation_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "delegated_pending",
            "stage": "delegated",
            "join_policy": "first",
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
                }
            ],
        }
        tools = FakeAgentTools(
            participants=[{"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"}],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: delegation_payload},
                }
            ],
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow(
                {"decision": "synthesize", "content": "first answer", "mentions": []}
            ),
            state_source=RestCrewAIFlowStateSource(),
            join_policy="first",
        )
        await _start(adapter, "router")
        await _turn(
            adapter,
            tools,
            _msg(
                id="msg-reply-A",
                content="here is A's answer",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            is_session_bootstrap=False,
        )

        assert tools.messages_sent == [
            {"id": "msg-0", "content": "first answer", "mentions": ["@example/peer-a"]}
        ]
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "reply_recorded" in statuses
        assert "finalized" in statuses

    @pytest.mark.asyncio
    async def test_ambiguous_agent_reply_records_reply_ambiguous(self) -> None:
        ns = "crewai_flow:router"
        event_time = datetime.now(timezone.utc).isoformat()
        run_payloads = []
        for run_id in ("msg-parent-1", "msg-parent-2"):
            run_payloads.append(
                {
                    "schema_version": 1,
                    "room_id": "room-1",
                    "run_id": run_id,
                    "parent_message_id": run_id,
                    "status": "delegated_pending",
                    "stage": "delegated",
                    "join_policy": "all",
                    "delegations": [
                        {
                            "delegation_id": f"{run_id}:d-A",
                            "target": {
                                "participant_id": "p-a",
                                "handle": "@example/peer-a",
                                "normalized_key": "peer-a",
                            },
                            "status": "pending",
                            "side_effect_key": f"{run_id}:delegate:d-A",
                            "delegation_message_id": f"msg-deleg-{run_id}",
                        }
                    ],
                }
            )
        tools = FakeAgentTools(
            participants=[{"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"}],
            room_context=[
                {
                    "id": f"evt-{idx}",
                    "message_type": "task",
                    "inserted_at": event_time,
                    "metadata": {ns: payload},
                }
                for idx, payload in enumerate(run_payloads)
            ],
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow(
                {
                    "decision": "direct_response",
                    "content": "should not run",
                    "mentions": [],
                }
            ),
            state_source=RestCrewAIFlowStateSource(),
        )
        await _start(adapter, "router")
        await _turn(
            adapter,
            tools,
            _msg(
                id="msg-reply-A",
                content="ambiguous answer",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            is_session_bootstrap=False,
        )

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert statuses == ["reply_ambiguous"]

    @pytest.mark.asyncio
    async def test_reserved_delegation_can_match_reply(self) -> None:
        ns = "crewai_flow:router"
        delegation_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "side_effect_reserved",
            "stage": "delegated",
            "join_policy": "first",
            "delegations": [
                {
                    "delegation_id": "d-A",
                    "target": {
                        "participant_id": "p-a",
                        "handle": "@example/peer-a",
                        "normalized_key": "peer-a",
                    },
                    "status": "reserved",
                    "side_effect_key": "msg-parent:delegate:d-A",
                }
            ],
        }
        tools = FakeAgentTools(
            participants=[{"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"}],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: delegation_payload},
                }
            ],
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow(
                {"decision": "synthesize", "content": "reserved reply", "mentions": []}
            ),
            state_source=RestCrewAIFlowStateSource(),
            join_policy="first",
        )
        await _start(adapter, "router")
        await _turn(
            adapter,
            tools,
            _msg(
                id="msg-reply-A",
                content="reply to reserved delegation",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            is_session_bootstrap=False,
        )

        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "reply_recorded" in statuses
        assert "finalized" in statuses

    @pytest.mark.asyncio
    async def test_duplicate_normalized_key_records_reply_ambiguous(self) -> None:
        ns = "crewai_flow:router"
        delegation_payload = {
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
                }
            ],
        }
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-a2", "handle": "@other/peer-a", "name": "Peer A2"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: delegation_payload},
                }
            ],
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow(
                {"decision": "synthesize", "content": "should not send", "mentions": []}
            ),
            state_source=RestCrewAIFlowStateSource(),
        )
        await _start(adapter, "router")
        await _turn(
            adapter,
            tools,
            _msg(
                id="msg-reply-A",
                content="ambiguous identity",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            is_session_bootstrap=False,
        )

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert statuses == ["reply_ambiguous"]

    @pytest.mark.asyncio
    async def test_user_typed_unmatched_starts_new_run(self) -> None:
        flow = _flow({"decision": "direct_response", "content": "hi", "mentions": []})
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(participants=[{"id": "p", "handle": "@example/x"}])
        await _start(adapter)
        # Even with no pending state, a User-typed sender should start a new run.
        await _turn(adapter, tools, _msg(sender_type="User"))
        assert len(tools.messages_sent) == 1

    @pytest.mark.asyncio
    async def test_agent_typed_unmatched_is_discarded_by_default(self) -> None:
        flow = _flow(
            {"decision": "direct_response", "content": "should not run", "mentions": []}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools()
        await _start(adapter)
        await _turn(
            adapter,
            tools,
            _msg(
                sender_id="@example/some-agent",
                sender_type="Agent",
                sender_name="Some Agent",
            ),
        )
        assert tools.messages_sent == []
        assert tools.events_sent == []

    @pytest.mark.asyncio
    async def test_agent_typed_unmatched_starts_new_run_when_enabled(self) -> None:
        flow = _flow(
            {"decision": "direct_response", "content": "agent routed", "mentions": []}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
            accept_agent_initiated=True,
        )
        tools = FakeAgentTools(participants=[{"id": "p", "handle": "@example/x"}])
        await _start(adapter)
        await _turn(
            adapter,
            tools,
            _msg(
                sender_id="@example/some-agent",
                sender_type="Agent",
                sender_name="Some Agent",
            ),
        )
        assert len(tools.messages_sent) == 1
        assert tools.messages_sent[0]["content"] == "agent routed"


# ---------------------------------------------------------------------------
# Indeterminate handling: reservation without sent record
# ---------------------------------------------------------------------------


class TestIndeterminate:
    @pytest.mark.asyncio
    async def test_reservation_without_sent_records_indeterminate_on_retry(
        self,
    ) -> None:
        ns = "crewai_flow:router"
        # State: reservation written but no sent confirmation for the same run.
        reservation_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-1",
            "parent_message_id": "msg-1",
            "status": "side_effect_reserved",
            "stage": "synthesizing",
            "final_side_effect_key": "msg-1:final",
        }
        tools = FakeAgentTools(
            participants=[{"id": "p", "handle": "@example/peer"}],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: reservation_payload},
                }
            ],
        )
        flow = _flow(
            {"decision": "direct_response", "content": "retry", "mentions": []}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=RestCrewAIFlowStateSource(),
        )
        await _start(adapter, "router")
        await _turn(adapter, tools, _msg(id="msg-1"), is_session_bootstrap=True)

        # No new visible message; an indeterminate task event is recorded.
        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "indeterminate" in statuses

    @pytest.mark.asyncio
    async def test_direct_response_send_failure_after_reservation_records_indeterminate(
        self,
    ) -> None:
        class FailingSendTools(FakeAgentTools):
            async def send_message(
                self,
                content: str,
                mentions: list[str] | list[dict[str, str]] | None = None,
            ) -> dict[str, Any]:
                raise RuntimeError("send failed")

        flow = _flow(
            {"decision": "direct_response", "content": "final", "mentions": []}
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FailingSendTools()
        await _start(adapter, "router")
        await _turn(adapter, tools, _msg(id="msg-1"), is_session_bootstrap=True)

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert statuses == ["side_effect_reserved", "indeterminate"]


class TestPersistedRunPolicy:
    @pytest.mark.asyncio
    async def test_existing_run_uses_persisted_join_policy_after_config_change(
        self,
    ) -> None:
        ns = "crewai_flow:router"
        delegation_payload = {
            "schema_version": 1,
            "room_id": "room-1",
            "run_id": "msg-parent",
            "parent_message_id": "msg-parent",
            "status": "delegated_pending",
            "stage": "delegated",
            "join_policy": "first",
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
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-b", "handle": "@example/peer-b", "name": "Peer B"},
            ],
            room_context=[
                {
                    "id": "evt-prior",
                    "message_type": "task",
                    "inserted_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": {ns: delegation_payload},
                }
            ],
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: _flow(
                {"decision": "synthesize", "content": "first answer", "mentions": []}
            ),
            state_source=RestCrewAIFlowStateSource(),
            join_policy="all",
        )
        await _start(adapter, "router")
        await _turn(
            adapter,
            tools,
            _msg(
                id="msg-reply-A",
                content="here is A's answer",
                sender_id="@example/peer-a",
                sender_type="Agent",
                sender_name="Peer A",
            ),
            is_session_bootstrap=False,
        )

        assert [m["content"] for m in tools.messages_sent] == ["first answer"]
        statuses = [
            e["metadata"].get(ns, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert "finalized" in statuses


class TestDelegationAmbiguity:
    @pytest.mark.asyncio
    async def test_ambiguous_delegation_target_records_failed_without_sending(
        self,
    ) -> None:
        flow = _flow(
            {
                "decision": "delegate",
                "delegations": [
                    {
                        "delegation_id": "d-A",
                        "target": "peer-a",
                        "content": "do A",
                        "mentions": ["@example/peer-a"],
                    }
                ],
            }
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FakeAgentTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-a2", "handle": "@other/peer-a", "name": "Peer A2"},
            ]
        )
        await _start(adapter, "router")
        await _turn(adapter, tools, _msg(id="msg-1"), is_session_bootstrap=True)

        assert tools.messages_sent == []
        payloads = [
            e["metadata"].get(adapter.metadata_namespace, {})
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert payloads[-1]["status"] == "failed"
        assert payloads[-1]["error"]["code"] == "ambiguous_participant"

    @pytest.mark.asyncio
    async def test_delegation_send_failure_stops_later_delegations(
        self,
    ) -> None:
        class FirstSendFailsTools(FakeAgentTools):
            async def send_message(
                self,
                content: str,
                mentions: list[str] | list[dict[str, str]] | None = None,
            ) -> dict[str, Any]:
                if not self.messages_sent:
                    raise RuntimeError("send failed")
                return await super().send_message(content, mentions)

        flow = _flow(
            {
                "decision": "delegate",
                "delegations": [
                    {
                        "delegation_id": "d-A",
                        "target": "peer-a",
                        "content": "do A",
                        "mentions": ["@example/peer-a"],
                    },
                    {
                        "delegation_id": "d-B",
                        "target": "peer-b",
                        "content": "do B",
                        "mentions": ["@example/peer-b"],
                    },
                ],
            }
        )
        adapter = CrewAIFlowAdapter(
            flow_factory=lambda: flow,
            state_source=HistoryCrewAIFlowStateSource(acknowledge_test_only=True),
        )
        tools = FirstSendFailsTools(
            participants=[
                {"id": "p-a", "handle": "@example/peer-a", "name": "Peer A"},
                {"id": "p-b", "handle": "@example/peer-b", "name": "Peer B"},
            ]
        )
        await _start(adapter, "router")
        await _turn(adapter, tools, _msg(id="msg-1"), is_session_bootstrap=True)

        assert tools.messages_sent == []
        statuses = [
            e["metadata"].get(adapter.metadata_namespace, {}).get("status")
            for e in tools.events_sent
            if e["message_type"] == "task"
        ]
        assert statuses == ["side_effect_reserved", "indeterminate"]
