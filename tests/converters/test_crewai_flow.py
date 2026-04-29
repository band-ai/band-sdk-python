"""Unit tests for CrewAI Flow state converter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from thenvoi.converters.crewai_flow import (
    CrewAIFlowAmbiguousIdentityError,
    CrewAIFlowBufferedSynthesis,
    CrewAIFlowDelegationState,
    CrewAIFlowDelegationStatus,
    CrewAIFlowJoinPolicy,
    CrewAIFlowMetadata,
    CrewAIFlowParticipantSnapshot,
    CrewAIFlowRunStatus,
    CrewAIFlowSequentialChainState,
    CrewAIFlowSideEffectState,
    CrewAIFlowStage,
    CrewAIFlowStateConverter,
    normalize_participant_key,
)

NS = "crewai_flow"


def _ev(
    *,
    id: str,
    inserted_at: datetime,
    payload: dict,
    namespace: str = NS,
    message_type: str = "task",
) -> dict:
    return {
        "id": id,
        "message_type": message_type,
        "inserted_at": inserted_at.isoformat(),
        "metadata": {namespace: payload},
    }


def _base_payload(
    *,
    run_id: str = "msg-1",
    status: CrewAIFlowRunStatus = CrewAIFlowRunStatus.OBSERVED,
    stage: CrewAIFlowStage = CrewAIFlowStage.INITIAL,
    delegations: list | None = None,
    final_message_id: str | None = None,
    error: dict | None = None,
) -> dict:
    payload: dict = {
        "schema_version": 1,
        "room_id": "room-1",
        "run_id": run_id,
        "parent_message_id": run_id,
        "status": status.value,
        "stage": stage.value,
    }
    if delegations is not None:
        payload["delegations"] = delegations
    if final_message_id is not None:
        payload["final_message_id"] = final_message_id
    if error is not None:
        payload["error"] = error
    return payload


def _delegation(
    *,
    delegation_id: str = "msg-1:peer-a",
    target_key: str = "peer-a",
    status: CrewAIFlowDelegationStatus = CrewAIFlowDelegationStatus.PENDING,
) -> dict:
    return {
        "delegation_id": delegation_id,
        "target": {
            "participant_id": f"id-{target_key}",
            "handle": f"@example/{target_key}",
            "normalized_key": target_key,
        },
        "status": status.value,
        "side_effect_key": f"msg-1:delegate:{target_key}",
    }


# ---------------------------------------------------------------------------
# convert(empty / unrelated)
# ---------------------------------------------------------------------------


class TestEmptyAndUnrelated:
    def test_empty_returns_empty_state(self) -> None:
        state = CrewAIFlowStateConverter().convert([])
        assert state.runs == {}

    def test_unrelated_events_ignored(self) -> None:
        events = [
            {
                "id": "e1",
                "message_type": "text",
                "metadata": {},
                "inserted_at": "2026-01-01T00:00:00Z",
            },
            {
                "id": "e2",
                "message_type": "task",
                "metadata": {"other": {"x": 1}},
                "inserted_at": "2026-01-01T00:00:00Z",
            },
        ]
        state = CrewAIFlowStateConverter().convert(events)
        assert state.runs == {}

    def test_other_namespace_ignored(self) -> None:
        events = [
            _ev(
                id="e1",
                inserted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload=_base_payload(),
                namespace="crewai_flow:other_agent",
            )
        ]
        state = CrewAIFlowStateConverter(metadata_namespace=NS).convert(events)
        assert state.runs == {}


# ---------------------------------------------------------------------------
# Reservation, pending, replied flow
# ---------------------------------------------------------------------------


class TestReservationAndDelegation:
    def test_reservation_then_pending_merge(self) -> None:
        t1 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=1)
        events = [
            _ev(
                id="e1",
                inserted_at=t1,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.SIDE_EFFECT_RESERVED,
                    stage=CrewAIFlowStage.INITIAL,
                    delegations=[
                        _delegation(status=CrewAIFlowDelegationStatus.RESERVED)
                    ],
                ),
            ),
            _ev(
                id="e2",
                inserted_at=t2,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                    stage=CrewAIFlowStage.DELEGATED,
                    delegations=[
                        _delegation(status=CrewAIFlowDelegationStatus.PENDING)
                    ],
                ),
            ),
        ]
        state = CrewAIFlowStateConverter().convert(events)
        run = state.runs["msg-1"]
        assert run.status == CrewAIFlowRunStatus.DELEGATED_PENDING
        assert run.stage == CrewAIFlowStage.DELEGATED
        assert len(run.delegations) == 1
        assert run.delegations[0].status == CrewAIFlowDelegationStatus.PENDING

    def test_message_id_breaks_inserted_at_ties(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Out-of-order insertion: REST returns e2 (later id) before e1.
        events = [
            _ev(
                id="e2",
                inserted_at=t,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                    stage=CrewAIFlowStage.DELEGATED,
                ),
            ),
            _ev(
                id="e1",
                inserted_at=t,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.SIDE_EFFECT_RESERVED,
                    stage=CrewAIFlowStage.INITIAL,
                ),
            ),
        ]
        state = CrewAIFlowStateConverter().convert(events)
        # e1 sorts first, e2 last; final state is from e2.
        assert state.runs["msg-1"].status == CrewAIFlowRunStatus.DELEGATED_PENDING


# ---------------------------------------------------------------------------
# Terminal absorption
# ---------------------------------------------------------------------------


class TestTerminalAbsorption:
    @pytest.mark.parametrize(
        "terminal",
        [
            CrewAIFlowRunStatus.FINALIZED,
            CrewAIFlowRunStatus.FAILED,
            CrewAIFlowRunStatus.INDETERMINATE,
        ],
    )
    def test_terminal_states_absorb_later_events(
        self, terminal: CrewAIFlowRunStatus
    ) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=1)
        events = [
            _ev(
                id="e1",
                inserted_at=t1,
                payload=_base_payload(status=terminal, stage=CrewAIFlowStage.DONE),
            ),
            _ev(
                id="e2",
                inserted_at=t2,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                    stage=CrewAIFlowStage.DELEGATED,
                ),
            ),
        ]
        state = CrewAIFlowStateConverter().convert(events)
        assert state.runs["msg-1"].status == terminal

    def test_terminal_short_circuit_perf(self) -> None:
        # 1,000-event log with 100 terminal runs converts under reasonable time.
        events = []
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(100):
            run_id = f"run-{i}"
            events.append(
                _ev(
                    id=f"r{i}-init",
                    inserted_at=base + timedelta(seconds=i),
                    payload=_base_payload(
                        run_id=run_id,
                        status=CrewAIFlowRunStatus.FINALIZED,
                        stage=CrewAIFlowStage.DONE,
                    ),
                )
            )
            for j in range(9):
                events.append(
                    _ev(
                        id=f"r{i}-spam-{j}",
                        inserted_at=base + timedelta(seconds=i, milliseconds=j),
                        payload=_base_payload(
                            run_id=run_id,
                            status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                            stage=CrewAIFlowStage.DELEGATED,
                        ),
                    )
                )

        state = CrewAIFlowStateConverter().convert(events)
        assert len(state.runs) == 100
        for r in state.runs.values():
            assert r.status == CrewAIFlowRunStatus.FINALIZED


# ---------------------------------------------------------------------------
# Malformed metadata
# ---------------------------------------------------------------------------


class TestMalformedMetadata:
    def test_malformed_payload_records_failed(self) -> None:
        events = [
            {
                "id": "e1",
                "message_type": "task",
                "inserted_at": datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat(),
                "metadata": {
                    NS: {
                        "schema_version": 1,
                        "room_id": "room-1",
                        "run_id": "msg-bad",
                        "parent_message_id": "msg-bad",
                        # Invalid status string.
                        "status": "not_a_status",
                        "stage": CrewAIFlowStage.INITIAL.value,
                    }
                },
            }
        ]
        state = CrewAIFlowStateConverter().convert(events)
        run = state.runs["msg-bad"]
        assert run.status == CrewAIFlowRunStatus.FAILED
        assert run.error is not None
        assert run.error.code == "malformed_metadata"

    def test_malformed_payload_after_terminal_run_is_ignored(self) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=1)
        events = [
            _ev(
                id="e1",
                inserted_at=t1,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.FINALIZED,
                    stage=CrewAIFlowStage.DONE,
                    final_message_id="msg-final",
                ),
            ),
            {
                "id": "e2",
                "message_type": "task",
                "inserted_at": t2.isoformat(),
                "metadata": {
                    NS: {
                        "schema_version": 1,
                        "room_id": "room-1",
                        "run_id": "msg-1",
                        "parent_message_id": "msg-1",
                        "status": "not_a_status",
                        "stage": CrewAIFlowStage.INITIAL.value,
                    }
                },
            },
        ]
        state = CrewAIFlowStateConverter().convert(events)
        run = state.runs["msg-1"]
        assert run.status == CrewAIFlowRunStatus.FINALIZED
        assert run.final_message_id == "msg-final"


# ---------------------------------------------------------------------------
# Buffered syntheses + sequential chains
# ---------------------------------------------------------------------------


class TestBufferedAndChains:
    def test_buffered_syntheses_merge_by_source_id(self) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=1)
        events = [
            _ev(
                id="e1",
                inserted_at=t1,
                payload={
                    **_base_payload(
                        status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                        stage=CrewAIFlowStage.DELEGATED,
                    ),
                    "buffered_syntheses": [
                        {"source_message_id": "m1", "content": "first"}
                    ],
                },
            ),
            _ev(
                id="e2",
                inserted_at=t2,
                payload={
                    **_base_payload(
                        status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                        stage=CrewAIFlowStage.DELEGATED,
                    ),
                    "buffered_syntheses": [
                        {"source_message_id": "m2", "content": "second"}
                    ],
                },
            ),
        ]
        state = CrewAIFlowStateConverter().convert(events)
        run = state.runs["msg-1"]
        assert [b.source_message_id for b in run.buffered_syntheses] == ["m1", "m2"]

    def test_side_effects_merge_by_key(self) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = t1 + timedelta(seconds=1)
        events = [
            _ev(
                id="e1",
                inserted_at=t1,
                payload={
                    **_base_payload(
                        status=CrewAIFlowRunStatus.SIDE_EFFECT_RESERVED,
                        stage=CrewAIFlowStage.DELEGATED,
                    ),
                    "side_effects": [{"side_effect_key": "msg-1:subcrew:1"}],
                },
            ),
            _ev(
                id="e2",
                inserted_at=t2,
                payload={
                    **_base_payload(
                        status=CrewAIFlowRunStatus.WAITING,
                        stage=CrewAIFlowStage.WAITING_FOR_REPLIES,
                    ),
                    "side_effects": [
                        {
                            "side_effect_key": "msg-1:subcrew:1",
                            "message_id": "msg-subcrew",
                        }
                    ],
                },
            ),
        ]
        state = CrewAIFlowStateConverter().convert(events)
        assert state.runs["msg-1"].side_effects[0].message_id == "msg-subcrew"

    def test_side_effects_merge_preserves_confirmed_fields_on_ties(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [
            _ev(
                id="e2",
                inserted_at=t,
                payload={
                    **_base_payload(
                        status=CrewAIFlowRunStatus.WAITING,
                        stage=CrewAIFlowStage.WAITING_FOR_REPLIES,
                    ),
                    "side_effects": [
                        {
                            "side_effect_key": "msg-1:subcrew:1",
                            "message_id": "msg-subcrew",
                        }
                    ],
                },
            ),
            _ev(
                id="e1",
                inserted_at=t,
                payload={
                    **_base_payload(
                        status=CrewAIFlowRunStatus.SIDE_EFFECT_RESERVED,
                        stage=CrewAIFlowStage.DELEGATED,
                    ),
                    "side_effects": [{"side_effect_key": "msg-1:subcrew:1"}],
                },
            ),
        ]
        state = CrewAIFlowStateConverter().convert(events)
        assert state.runs["msg-1"].side_effects[0].message_id == "msg-subcrew"


# ---------------------------------------------------------------------------
# max_run_age proactive close
# ---------------------------------------------------------------------------


class TestMaxRunAge:
    def test_run_aged_out(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=30)
        events = [
            _ev(
                id="e1",
                inserted_at=old,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                    stage=CrewAIFlowStage.DELEGATED,
                ),
            )
        ]
        converter = CrewAIFlowStateConverter(max_run_age=timedelta(days=7))
        state = converter.convert(events)
        run = state.runs["msg-1"]
        assert run.status == CrewAIFlowRunStatus.FAILED
        assert run.error is not None
        assert run.error.code == "run_aged_out"

    def test_terminal_run_not_aged_out(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=30)
        events = [
            _ev(
                id="e1",
                inserted_at=old,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.FINALIZED,
                    stage=CrewAIFlowStage.DONE,
                ),
            )
        ]
        state = CrewAIFlowStateConverter(max_run_age=timedelta(days=7)).convert(events)
        assert state.runs["msg-1"].status == CrewAIFlowRunStatus.FINALIZED


# ---------------------------------------------------------------------------
# Restart fixture: fresh converter from raw events reconstructs state
# ---------------------------------------------------------------------------


class TestRestart:
    def test_restart_from_raw_events(self) -> None:
        t = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = [
            _ev(
                id="e1",
                inserted_at=t,
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.SIDE_EFFECT_RESERVED,
                    stage=CrewAIFlowStage.INITIAL,
                    delegations=[
                        _delegation(status=CrewAIFlowDelegationStatus.RESERVED)
                    ],
                ),
            ),
            _ev(
                id="e2",
                inserted_at=t + timedelta(seconds=1),
                payload=_base_payload(
                    status=CrewAIFlowRunStatus.DELEGATED_PENDING,
                    stage=CrewAIFlowStage.DELEGATED,
                    delegations=[
                        _delegation(status=CrewAIFlowDelegationStatus.PENDING)
                    ],
                ),
            ),
        ]
        # Two converters, no shared memory: same input → same output.
        a = CrewAIFlowStateConverter().convert(events)
        b = CrewAIFlowStateConverter().convert(events)
        assert a.model_dump() == b.model_dump()


# ---------------------------------------------------------------------------
# normalize_participant_key
# ---------------------------------------------------------------------------


class TestNormalizeParticipantKey:
    def test_strip_at_and_namespace(self) -> None:
        assert normalize_participant_key("@example/peer-a") == "peer-a"
        assert normalize_participant_key("peer-a") == "peer-a"
        assert normalize_participant_key("@peer-a") == "peer-a"
        assert normalize_participant_key("  PEER-A  ") == "peer-a"

    def test_uuid_routes_through_handle(self) -> None:
        participants = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "handle": "@example/peer-a",
                "name": "Peer A",
            }
        ]
        result = normalize_participant_key(
            "11111111-1111-1111-1111-111111111111",
            participants=participants,
        )
        assert result == "peer-a"

    def test_display_name_routes_through_handle(self) -> None:
        participants = [
            {
                "id": "id-1",
                "handle": "@example/peer-a",
                "name": "Peer A",
            }
        ]
        assert (
            normalize_participant_key("Peer A", participants=participants) == "peer-a"
        )

    def test_collision_raises(self) -> None:
        participants = [
            {"id": "id-1", "handle": "@example/peer-a", "name": "P1"},
            {"id": "id-2", "handle": "@other/peer-a", "name": "P2"},
        ]
        with pytest.raises(CrewAIFlowAmbiguousIdentityError):
            normalize_participant_key("peer-a", participants=participants)


# ---------------------------------------------------------------------------
# Sanity: model exports
# ---------------------------------------------------------------------------


def test_models_exportable() -> None:
    assert CrewAIFlowMetadata
    assert CrewAIFlowDelegationState
    assert CrewAIFlowSequentialChainState
    assert CrewAIFlowSideEffectState
    assert CrewAIFlowBufferedSynthesis
    assert CrewAIFlowParticipantSnapshot
    assert CrewAIFlowJoinPolicy
