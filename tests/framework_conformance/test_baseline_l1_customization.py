"""L1 customization baseline conformance rows."""

from __future__ import annotations

import pytest

from thenvoi.adapters.anthropic import AnthropicAdapter

from tests.baseline_l1_fixtures import L1_CUSTOM_TOOL_NAME, LogKeywordInput
from tests.framework_conformance.baseline_applicability import (
    ApplicabilityStatus,
    build_applicability_matrix,
)
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID
from tests.framework_conformance.baseline_status import ScenarioKind
from tests.framework_conformance.dispatch_capture import (
    HONEST_DISPATCH_ADAPTER_IDS,
    dispatch_l1_custom_tool,
)
from tests.framework_conformance.platform_fixtures import (
    ROOM_ID,
    build_agent_input_through_preprocessor,
    canonical_participants,
    canonical_peers,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_ADAPTER_IDS,
    CapturedRequest,
    ConformanceSchemaRecorder,
    capture_request,
)

_L1_REQUEST_ROWS = {
    "L1.request.custom_prompt_present",
    "L1.request.custom_prompt_additive",
}

_L1_CUSTOM_PROMPT = "L1 unique additive prompt sentinel."


def _required_system_text(system_text: str | None) -> str:
    if not system_text:
        raise AssertionError("adapter did not expose model-visible system text")
    return system_text


async def _capture_l1_request(adapter_id: str) -> CapturedRequest:
    agent_input = await build_agent_input_through_preprocessor()
    return await capture_request(
        adapter_id,
        agent_input,
        custom_prompt=_L1_CUSTOM_PROMPT,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l1_custom_prompt_present_in_system_prompt_by_default(
    adapter_id: str,
) -> None:
    captured = await _capture_l1_request(adapter_id)

    assert _L1_CUSTOM_PROMPT in _required_system_text(captured.system_text)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", REQUEST_CAPTURE_ADAPTER_IDS)
async def test_l1_custom_prompt_is_additive_with_base_and_identity_by_default(
    adapter_id: str,
) -> None:
    captured = await _capture_l1_request(adapter_id)
    system_text = _required_system_text(captured.system_text)

    assert "Test Agent" in system_text
    assert "conformance test agent" in system_text
    assert (
        "Treat messages from other participants as user input" in system_text
        or "Plain text responses will NOT be delivered" in system_text
    )
    assert "mentions" in system_text
    assert captured.base_instruction_surface is not None


def test_l1_explicit_full_override_is_separate_from_default_additive_prompt() -> None:
    adapter = AnthropicAdapter(
        provider_key="test-provider-key",
        system_prompt="Use only this explicit override.",
        prompt=_L1_CUSTOM_PROMPT,
    )

    assert adapter.system_prompt == "Use only this explicit override."
    assert adapter._prompt == _L1_CUSTOM_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter_id", HONEST_DISPATCH_ADAPTER_IDS)
async def test_l1_custom_tool_dispatch_reaches_developer_handler(
    adapter_id: str,
) -> None:
    message = f"L1-CUSTOM-{adapter_id}"
    tools = ConformanceSchemaRecorder(
        participants=canonical_participants(),
        peers=canonical_peers(),
        room_id=ROOM_ID,
    )

    result = await dispatch_l1_custom_tool(adapter_id, message=message, tools=tools)

    assert result.calls == [LogKeywordInput(message=message)]
    assert result.tool_calls == []


def test_l1_scorecard_has_reviewed_request_and_dispatch_cells() -> None:
    l1_ids = {
        scenario.id
        for scenario in SCENARIOS_BY_ID.values()
        if scenario.level.value == "l1"
    }
    l1_cells = [
        cell for cell in build_applicability_matrix() if cell.scenario_id in l1_ids
    ]

    assert l1_cells
    assert all(
        cell.status is not ApplicabilityStatus.UNKNOWN_FAIL_CLOSED for cell in l1_cells
    )
    dispatch_cells = [
        cell
        for cell in l1_cells
        if SCENARIOS_BY_ID[cell.scenario_id].kind is ScenarioKind.DISPATCH
    ]
    runtime_owned_dispatch = [
        cell
        for cell in dispatch_cells
        if cell.status is ApplicabilityStatus.TIER2_BLOCKED
    ]
    assert runtime_owned_dispatch
    assert all(
        cell.reason and not cell.tier2_pointer for cell in runtime_owned_dispatch
    )


def test_l1_rows_are_registered_as_core_contract() -> None:
    for row_id in _L1_REQUEST_ROWS:
        scenario = SCENARIOS_BY_ID[row_id]
        assert scenario.core_contract is True
        assert scenario.requires_request_capture is True

    custom_tool = SCENARIOS_BY_ID["L1.dispatch.custom_tool"]
    assert custom_tool.core_contract is True
    assert custom_tool.applies_to_dispatch_bindings is True
    assert custom_tool.required_tools == frozenset({L1_CUSTOM_TOOL_NAME})
