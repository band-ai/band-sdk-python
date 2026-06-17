"""L3 multi-participant baseline conformance rows."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tests.framework_conformance.baseline_applicability import (
    ApplicabilityStatus,
    build_applicability_matrix,
)
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID
from tests.framework_conformance.baseline_status import BaselineContract
from tests.framework_conformance.platform_fixtures import (
    AGENT_ID,
    PEER_AGENT_ID,
    SECOND_PEER_AGENT_ID,
    SECOND_PEER_AGENT_HANDLE,
    USER_ID,
    ConformanceExecutionContext,
    build_agent_input_through_preprocessor,
    canonical_participants,
    current_message_event,
    history_message,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_ADAPTER_IDS,
    CapturedRequest,
    assert_token_order,
    capture_request,
    token_position,
    visible_text,
)

_CURRENT_L3_ID = "msg-l3-current-trigger"


def _participants_with_greeter() -> list[dict[str, Any]]:
    return [
        *canonical_participants(),
        {
            "id": SECOND_PEER_AGENT_ID,
            "name": "Greeter",
            "type": "Agent",
            "handle": SECOND_PEER_AGENT_HANDLE,
            "description": "Writes greeting-card copy for named recipients.",
        },
    ]


def _multi_author_history() -> list[dict[str, Any]]:
    return [
        history_message(
            message_id="msg-l3-user-001",
            content="USER-L3-TURN asked for room coordination.",
            sender_id=USER_ID,
            sender_type="User",
            sender_name="Darvell",
            offset_seconds=1,
        ),
        history_message(
            message_id="msg-l3-calc-001",
            content="CALC-L3-TURN supplied the calculation result.",
            sender_id=PEER_AGENT_ID,
            sender_type="Agent",
            sender_name="Calc",
            offset_seconds=2,
        ),
        history_message(
            message_id="msg-l3-greeter-001",
            content="GREETER-L3-TURN supplied the greeting text.",
            sender_id=SECOND_PEER_AGENT_ID,
            sender_type="Agent",
            sender_name="Greeter",
            offset_seconds=3,
        ),
    ]


async def _capture_l3_request(adapter_id: str) -> CapturedRequest:
    ctx = ConformanceExecutionContext(
        history_messages=_multi_author_history(),
        participants=_participants_with_greeter(),
    )
    agent_input = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(
            content="@darvell/test-agent please coordinate with @darvell/calc and @darvell/greeter.",
            message_id=_CURRENT_L3_ID,
        ),
        agent_id=AGENT_ID,
    )
    return await capture_request(adapter_id, agent_input)


def _assert_l3_roster_oracle(captured: CapturedRequest) -> None:
    text = visible_text(captured)
    assert "## Current Participants" in text
    assert "@darvell — Darvell (User)" in text
    assert "@darvell/test-agent — Test Agent (Agent)" in text
    assert "@darvell/calc — Calc (Agent)" in text
    assert (
        "@darvell/greeter — Greeter (Agent): Writes greeting-card copy for named recipients."
        in text
    )
    assert "NOT the display name" in text
    assert "@username/agent-slug" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l3_roster_handles_and_types_emit_on_bootstrap_participant_change(
    adapter_id: str,
) -> None:
    captured = await _capture_l3_request(adapter_id)

    _assert_l3_roster_oracle(captured)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l3_mention_convention_reaches_adapter_payloads(
    adapter_id: str,
) -> None:
    captured = await _capture_l3_request(adapter_id)

    text = visible_text(captured)
    assert "@darvell/calc" in text
    assert "@darvell/greeter" in text
    assert "NOT the display name" in text
    assert "@username/agent-slug" not in text


@pytest.mark.asyncio
async def test_l3_roster_oracle_rejects_missing_participant_roster() -> None:
    captured = await _capture_l3_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(
        captured,
        message_texts=[
            text.replace("## Current Participants", "## Hidden Participants")
            for text in captured.message_texts
        ],
    )

    with pytest.raises(AssertionError):
        _assert_l3_roster_oracle(mutated)


@pytest.mark.asyncio
async def test_l3_roster_oracle_rejects_display_name_only_mentions() -> None:
    captured = await _capture_l3_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(
        captured,
        message_texts=[
            text.replace("@darvell/calc", "Calc").replace("@darvell/greeter", "Greeter")
            for text in captured.message_texts
        ],
    )

    with pytest.raises(AssertionError):
        _assert_l3_roster_oracle(mutated)


@pytest.mark.asyncio
async def test_l3_roster_oracle_rejects_missing_participant_description() -> None:
    captured = await _capture_l3_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(
        captured,
        message_texts=[
            text.replace(": Writes greeting-card copy for named recipients.", "")
            for text in captured.message_texts
        ],
    )

    with pytest.raises(AssertionError):
        _assert_l3_roster_oracle(mutated)


@pytest.mark.asyncio
async def test_l3_mention_convention_suppresses_steady_state_roster() -> None:
    ctx = ConformanceExecutionContext(
        history_messages=_multi_author_history(),
        participants=_participants_with_greeter(),
    )
    first = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(message_id="msg-l3-first"),
        agent_id=AGENT_ID,
    )
    second = await build_agent_input_through_preprocessor(
        ctx=ctx,
        event=current_message_event(message_id="msg-l3-second"),
        agent_id=AGENT_ID,
    )

    assert first.participants_msg is not None
    assert "@darvell/calc" in first.participants_msg
    assert "@darvell/greeter" in first.participants_msg
    assert "NOT the display name" in first.participants_msg
    assert "@username/agent-slug" not in first.participants_msg
    assert second.participants_msg is None


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l3_multi_author_history_order_and_attribution_reach_adapter_payloads(
    adapter_id: str,
) -> None:
    captured = await _capture_l3_request(adapter_id)

    assert_token_order(
        captured,
        "USER-L3-TURN",
        "CALC-L3-TURN",
        "GREETER-L3-TURN",
        "@darvell/greeter",
    )
    text = visible_text(captured)
    assert "[Darvell]:" in text
    assert "[Calc]:" in text
    assert "[Greeter]:" in text

    # Role assertions apply only to discrete-message captures; flattened and
    # engine-input captures carry attribution via the speaker labels asserted
    # above. The shape/role-support pairing is enforced for every probe by
    # test_l2_speaker_role_opt_out_is_justified_by_capture_shape.
    if captured.supports_speaker_roles:
        user, _ = token_position(captured, "USER-L3-TURN")
        calc, _ = token_position(captured, "CALC-L3-TURN")
        greeter, _ = token_position(captured, "GREETER-L3-TURN")
        assert captured.message_roles[user] == "user"
        assert captured.message_roles[calc] in {"user", "human"}
        assert captured.message_roles[greeter] in {"user", "human"}


def test_l3_scorecard_separates_core_rows_from_hardening_no_wake_row() -> None:
    l3_rows = [
        scenario
        for scenario in SCENARIOS_BY_ID.values()
        if scenario.domain_contract is BaselineContract.L3_MULTI_PARTICIPANT
    ]
    assert {scenario.id for scenario in l3_rows} == {
        "L3.request.roster_handles",
        "L3.request.mention_convention",
        "L3.request.multi_author_history",
    }
    assert all(scenario.core_contract is True for scenario in l3_rows)

    no_wake = SCENARIOS_BY_ID["L3.runtime.no_wake_non_messages"]
    assert no_wake.core_contract is False
    assert no_wake.domain_contract is BaselineContract.SDK_HARDENING

    covered = [
        cell
        for cell in build_applicability_matrix()
        if cell.scenario_id == "L3.runtime.no_wake_non_messages"
    ]
    assert covered
    covered_by_existing = [
        cell
        for cell in covered
        if cell.status is ApplicabilityStatus.COVERED_BY_EXISTING
    ]
    excluded_bridges = [
        cell for cell in covered if cell.status is ApplicabilityStatus.EXCLUDED_BRIDGE
    ]
    assert covered_by_existing
    assert excluded_bridges
    assert len(covered_by_existing) + len(excluded_bridges) == len(covered)
    assert all(cell.covered_by_existing is not None for cell in covered_by_existing)
