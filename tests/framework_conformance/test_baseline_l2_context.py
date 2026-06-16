"""L2 conversation-context fidelity baseline conformance rows."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID
from tests.framework_conformance.platform_fixtures import (
    AGENT_ID,
    PEER_AGENT_ID,
    USER_ID,
    ConformanceExecutionContext,
    build_agent_input_through_preprocessor,
    current_message_event,
    history_message,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_ADAPTER_IDS,
    REQUEST_CAPTURE_PROBES,
    CapturedRequest,
    capture_request,
)

_LONG_HISTORY_IDS = [f"msg-l2-history-{index:03d}" for index in range(10)]
_CURRENT_L2_ID = "msg-l2-current-trigger"
_CURRENT_L2_SENTINEL = "CURRENT-L2-RECALL"
_CAPTURE_CACHE: dict[str, CapturedRequest] = {}


def _long_history() -> list[dict[str, Any]]:
    senders = [
        (USER_ID, "User", "Darvell"),
        (AGENT_ID, "Agent", "Test Agent"),
        (PEER_AGENT_ID, "Agent", "Calc"),
    ]
    messages = []
    for index, message_id in enumerate(_LONG_HISTORY_IDS):
        sender_id, sender_type, sender_name = senders[index % len(senders)]
        if index == 0:
            content = "EARLIEST-L2-TURN planted MARCO for recall."
        elif index == 4:
            content = "MIDDLE-L2-TURN planted LIGHTHOUSE for recall."
        elif index == 8:
            content = "LATEST-L2-TURN planted POSTGRESQL for recall."
        else:
            content = f"L2_TURN_{index:02d} context filler."
        messages.append(
            history_message(
                message_id=message_id,
                content=content,
                sender_id=sender_id,
                sender_type=sender_type,
                sender_name=sender_name,
                offset_seconds=index + 1,
            )
        )
    return messages


def _out_of_order_long_history() -> list[dict[str, Any]]:
    history = _long_history()
    return [
        history[4],
        history[0],
        history[9],
        history[2],
        history[1],
        history[8],
        *history[3:4],
        *history[5:8],
    ]


async def _capture_l2_request(adapter_id: str) -> CapturedRequest:
    if adapter_id not in _CAPTURE_CACHE:
        ctx = ConformanceExecutionContext(history_messages=_out_of_order_long_history())
        agent_input = await build_agent_input_through_preprocessor(
            ctx=ctx,
            event=current_message_event(
                content=f"@darvell/test-agent please recall {_CURRENT_L2_SENTINEL}.",
                message_id=_CURRENT_L2_ID,
            ),
        )
        _CAPTURE_CACHE[adapter_id] = await capture_request(adapter_id, agent_input)
    return _CAPTURE_CACHE[adapter_id]


def _visible_text(captured: CapturedRequest) -> str:
    return "\n".join([captured.system_text or "", *captured.message_texts])


def _token_position(captured: CapturedRequest, token: str) -> tuple[int, int]:
    for index, text in enumerate(captured.message_texts):
        offset = text.find(token)
        if offset != -1:
            return index, offset
    raise AssertionError(
        f"{token!r} not found in captured message texts: {captured.message_texts}"
    )


def _assert_token_order(captured: CapturedRequest, *tokens: str) -> None:
    positions = [_token_position(captured, token) for token in tokens]
    assert positions == sorted(positions)


def _expected_l2_source_ids() -> list[str]:
    return [*_LONG_HISTORY_IDS, _CURRENT_L2_ID]


def _assert_l2_source_counts(captured: CapturedRequest) -> None:
    expected_ids = _expected_l2_source_ids()
    assert captured.rehydration is not None
    source_counts = captured.rehydration.source_message_counts
    assert set(source_counts) == set(expected_ids)
    for message_id in expected_ids:
        assert source_counts[message_id] == 1


def _assert_l2_recall_oracle(captured: CapturedRequest) -> None:
    expected_ids = _expected_l2_source_ids()
    text = _visible_text(captured)
    assert captured.message_ids == expected_ids
    _assert_l2_source_counts(captured)
    for token in (
        "EARLIEST-L2-TURN",
        "MIDDLE-L2-TURN",
        "LATEST-L2-TURN",
        _CURRENT_L2_SENTINEL,
    ):
        assert token in text
    _assert_token_order(
        captured,
        "EARLIEST-L2-TURN",
        "MIDDLE-L2-TURN",
        "LATEST-L2-TURN",
        _CURRENT_L2_SENTINEL,
    )


def _source_items_by_id(captured: CapturedRequest) -> dict[str, list[Any]]:
    items_by_id: dict[str, list[Any]] = {}
    for item in captured.items:
        if item.source_message_id:
            items_by_id.setdefault(item.source_message_id, []).append(item)
    return items_by_id


def _expected_l2_roles() -> dict[str, set[str]]:
    roles: dict[str, set[str]] = {}
    for message in _long_history():
        message_id = str(message["id"])
        if message["sender_id"] == AGENT_ID:
            roles[message_id] = {"assistant", "ai", "model"}
        else:
            roles[message_id] = {"user", "human"}
    roles[_CURRENT_L2_ID] = {"user", "human"}
    return roles


def _history_label_content_pairs() -> list[tuple[str, str]]:
    return [
        (str(message["sender_name"]), str(message["content"]))
        for message in _long_history()
    ]


def _assert_l2_speaker_attribution_oracle(captured: CapturedRequest) -> None:
    text = _visible_text(captured)
    if captured.supports_speaker_roles:
        items_by_id = _source_items_by_id(captured)
        for message_id, allowed_roles in _expected_l2_roles().items():
            matching_items = items_by_id.get(message_id, [])
            assert matching_items, f"{message_id} missing from captured request items"
            assert {item.role for item in matching_items} <= allowed_roles
        return

    for sender_name, content in _history_label_content_pairs():
        if sender_name == "Test Agent":
            assert content in text
            continue
        assert f"[{sender_name}]: {content}" in text
    for sender_name, content in _history_label_content_pairs():
        if sender_name == "Test Agent":
            continue
        assert f"assistant: [{sender_name}]: {content}" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l2_full_history_and_earliest_turn_reach_adapter_payloads(
    adapter_id: str,
) -> None:
    captured = await _capture_l2_request(adapter_id)

    _assert_l2_recall_oracle(captured)
    assert captured.seam_owner.value in {"adapter_payload", "adapter_input"}


@pytest.mark.asyncio
async def test_l2_recall_oracle_rejects_dropped_earliest_turn() -> None:
    captured = await _capture_l2_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(
        captured,
        message_ids=[
            message_id
            for message_id in captured.message_ids
            if message_id != _LONG_HISTORY_IDS[0]
        ],
        message_texts=[
            text.replace("EARLIEST-L2-TURN planted MARCO for recall.", "")
            for text in captured.message_texts
        ],
    )

    with pytest.raises(AssertionError):
        _assert_l2_recall_oracle(mutated)


@pytest.mark.asyncio
async def test_l2_recall_oracle_rejects_reversed_chronology() -> None:
    captured = await _capture_l2_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(captured, message_texts=list(reversed(captured.message_texts)))

    with pytest.raises(AssertionError):
        _assert_l2_recall_oracle(mutated)


@pytest.mark.asyncio
async def test_l2_recall_oracle_rejects_duplicated_source_turn() -> None:
    captured = await _capture_l2_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    assert captured.rehydration is not None
    source_counts = dict(captured.rehydration.source_message_counts)
    source_counts[_LONG_HISTORY_IDS[4]] = 2
    mutated = replace(
        captured,
        rehydration=replace(captured.rehydration, source_message_counts=source_counts),
    )

    with pytest.raises(AssertionError):
        _assert_l2_recall_oracle(mutated)


@pytest.mark.asyncio
async def test_l2_recall_oracle_rejects_missing_middle_turn() -> None:
    captured = await _capture_l2_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    assert captured.rehydration is not None
    missing_id = _LONG_HISTORY_IDS[5]
    source_counts = dict(captured.rehydration.source_message_counts)
    source_counts.pop(missing_id)
    mutated = replace(
        captured,
        message_ids=[
            message_id
            for message_id in captured.message_ids
            if message_id != missing_id
        ],
        rehydration=replace(captured.rehydration, source_message_counts=source_counts),
    )

    with pytest.raises(AssertionError):
        _assert_l2_recall_oracle(mutated)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l2_history_order_is_chronological_with_current_trigger_last(
    adapter_id: str,
) -> None:
    captured = await _capture_l2_request(adapter_id)

    _assert_l2_recall_oracle(captured)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l2_speaker_attribution_survives_adapter_payload_construction(
    adapter_id: str,
) -> None:
    captured = await _capture_l2_request(adapter_id)

    _assert_l2_speaker_attribution_oracle(captured)


@pytest.mark.asyncio
async def test_l2_speaker_attribution_oracle_rejects_wrong_flattened_speaker() -> None:
    captured = await _capture_l2_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(
        captured,
        supports_speaker_roles=False,
        message_texts=[
            text.replace(
                "[Calc]: LATEST-L2-TURN planted POSTGRESQL for recall.",
                "[Darvell]: LATEST-L2-TURN planted POSTGRESQL for recall.",
            )
            for text in captured.message_texts
        ],
    )

    with pytest.raises(AssertionError):
        _assert_l2_speaker_attribution_oracle(mutated)


@pytest.mark.asyncio
async def test_l2_speaker_attribution_oracle_rejects_peer_as_assistant_channel() -> (
    None
):
    captured = await _capture_l2_request(REQUEST_CAPTURE_ADAPTER_IDS[0])
    mutated = replace(
        captured,
        supports_speaker_roles=False,
        message_texts=[
            text.replace(
                "[Calc]: LATEST-L2-TURN planted POSTGRESQL for recall.",
                "assistant: [Calc]: LATEST-L2-TURN planted POSTGRESQL for recall.",
            )
            for text in captured.message_texts
        ],
    )

    with pytest.raises(AssertionError):
        _assert_l2_speaker_attribution_oracle(mutated)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l2_speaker_role_opt_out_is_justified_by_capture_shape(
    adapter_id: str,
) -> None:
    """A capture may skip per-message role assertions only when its family has
    no per-message role channel (history flattened into one prompt/engine
    input). Discrete-message captures must prove roles; flattened captures
    must keep attribution via speaker labels instead."""
    captured = await _capture_l2_request(adapter_id)
    probe = REQUEST_CAPTURE_PROBES[adapter_id]

    assert captured.history_shape == probe.history_shape
    assert captured.supports_speaker_roles == (captured.history_shape == "discrete"), (
        f"{adapter_id}: supports_speaker_roles={captured.supports_speaker_roles} "
        f"with history_shape={captured.history_shape}; role opt-out is only "
        "justified for flattened/engine_input captures"
    )


def test_l2_rows_are_registered_as_core_request_capture() -> None:
    for row_id in {
        "L2.request.full_history",
        "L2.request.earliest_turn",
        "L2.request.chronological_order",
        "L2.request.speaker_attribution",
    }:
        scenario = SCENARIOS_BY_ID[row_id]
        assert scenario.core_contract is True
        assert scenario.requires_request_capture is True
        assert scenario.kind.value == "request_read"
