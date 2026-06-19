"""Static adapter applicability profiles for baseline conformance rows."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from tests.framework_conformance.baseline_evidence import evidence_by_id
from tests.framework_conformance.baseline_scenarios import SCENARIOS, BaselineScenario
from tests.framework_conformance.baseline_status import (
    CoverageEvidence,
    CoveredByExisting,
    ScenarioKind,
)
from tests.framework_conformance.injection_registry import (
    INJECTION_BINDINGS,
    INJECTION_EXCLUDED_MODULES,
    Tier1Status,
)


class ApplicabilityStatus(str, Enum):
    APPLICABLE = "applicable"
    N_A_TIER2 = "n_a_tier2"
    EXCLUDED_BRIDGE = "excluded_bridge"
    UNKNOWN_FAIL_CLOSED = "unknown_fail_closed"
    TIER2_BLOCKED = "tier2_blocked"
    COVERED_BY_EXISTING = "covered_by_existing"


@dataclass(frozen=True, kw_only=True)
class AdapterApplicabilityProfile:
    adapter_id: str
    request_read_status: ApplicabilityStatus
    capture_family: str | None
    base_instruction_surface: str | None
    reason: str | None = None
    tier2_pointer: str | None = None
    covered_by_existing: dict[str, CoveredByExisting] = field(default_factory=dict)
    coverage_evidence_ids: dict[str, tuple[str, ...]] = field(default_factory=dict)
    scenario_overrides: dict[str, ApplicabilityStatus] = field(default_factory=dict)


@dataclass(frozen=True, kw_only=True)
class ApplicabilityCell:
    adapter_id: str
    scenario_id: str
    status: ApplicabilityStatus
    capture_family: str | None = None
    base_instruction_surface: str | None = None
    reason: str | None = None
    tier2_pointer: str | None = None
    covered_by_existing: CoveredByExisting | None = None
    coverage_evidence: tuple[CoverageEvidence, ...] = ()


_BRIDGE_REASON = "protocol bridge has a separate lifecycle outside ordinary L0-L4 rows"

_EXISTING_NO_WAKE = CoveredByExisting(
    test_path="tests/preprocessing/test_default.py",
    test_names=(
        "test_returns_none_for_room_added_event",
        "test_returns_none_for_participant_added_event",
        "test_skips_own_agent_messages",
        "test_skips_unmentioned_text_messages",
        "test_unmentioned_slash_commands_do_not_wake",
        "test_skips_non_text_message_events",
        "test_processes_synthetic_contact_hub_messages_without_mentions",
        "test_processes_mentioned_adapter_control_commands",
    ),
    assertion_summary=(
        "preprocessor tests cover non-message, self-authored, and unmentioned "
        "events not waking adapter handlers, while preserving synthetic hub and "
        "approval-command work"
    ),
)

_EXISTING_OFFLINE_PENDING = CoveredByExisting(
    test_path="tests/runtime/test_execution.py",
    test_names=("test_pending_next_message_present_in_context_still_executes",),
    assertion_summary=(
        "runtime tests cover a pending /next message executing as current work "
        "even when the same message appears in room context"
    ),
)

_EXISTING_HANDLED_DEDUPE = CoveredByExisting(
    test_path="tests/test_session_sync.py",
    test_names=(
        "test_duplicate_event_skipped",
        "test_duplicate_refreshes_lru_position",
        "test_sync_point_reached_clears_marker_and_keeps_cache",
    ),
    assertion_summary=(
        "session sync tests cover processed metadata and local dedupe preventing "
        "already handled messages from reopening as new work"
    ),
)

_EXISTING_CLEANUP_CRASH_BOUNDARY = CoveredByExisting(
    test_path="tests/runtime/test_execution.py",
    test_names=(
        "test_sync_processes_backlog_messages",
        "test_ws_replay_with_processed_metadata_is_not_reopened",
    ),
    assertion_summary=(
        "runtime restart tests cover recovery without depending on adapter "
        "cleanup from the previous process"
    ),
)

_GENERIC_COVERED_ROWS = {
    "L3.runtime.no_wake_non_messages": _EXISTING_NO_WAKE,
    "L4.runtime.cleanup_not_required_for_crash_correctness": (
        _EXISTING_CLEANUP_CRASH_BOUNDARY
    ),
}

_GENERIC_COVERAGE_EVIDENCE_IDS = {
    "L3.runtime.no_wake_non_messages": ("runtime.no_wake_preprocessor",),
    "L4.runtime.cleanup_not_required_for_crash_correctness": (
        "runtime.cleanup_crash_boundary",
    ),
}

_REQUEST_PROFILES: dict[str, AdapterApplicabilityProfile] = {
    "langgraph": AdapterApplicabilityProfile(
        adapter_id="langgraph",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="langchain_messages",
        base_instruction_surface="system_message",
    ),
    "pydantic_ai": AdapterApplicabilityProfile(
        adapter_id="pydantic_ai",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="pydantic_ai_agent_input",
        base_instruction_surface="system_prompt",
    ),
    "google_adk": AdapterApplicabilityProfile(
        adapter_id="google_adk",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="adk_content_request",
        base_instruction_surface="instruction",
    ),
    "anthropic": AdapterApplicabilityProfile(
        adapter_id="anthropic",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="anthropic_messages",
        base_instruction_surface="system",
    ),
    "gemini": AdapterApplicabilityProfile(
        adapter_id="gemini",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="gemini_contents",
        base_instruction_surface="system_instruction",
    ),
    "codex": AdapterApplicabilityProfile(
        adapter_id="codex",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="codex_prompt",
        base_instruction_surface="session_prompt_prefix",
    ),
    "crewai": AdapterApplicabilityProfile(
        adapter_id="crewai",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="crewai_task_prompt",
        base_instruction_surface="backstory",
    ),
    "parlant": AdapterApplicabilityProfile(
        adapter_id="parlant",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="parlant_session_message",
        base_instruction_surface="system_prompt",
    ),
    "claude_sdk": AdapterApplicabilityProfile(
        adapter_id="claude_sdk",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="claude_sdk_prompt",
        base_instruction_surface="system_prompt_append",
    ),
    "opencode": AdapterApplicabilityProfile(
        adapter_id="opencode",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="opencode_prompt_call",
        base_instruction_surface="system_prompt",
    ),
    "letta": AdapterApplicabilityProfile(
        adapter_id="letta",
        request_read_status=ApplicabilityStatus.APPLICABLE,
        capture_family="letta_agent_prompt",
        base_instruction_surface="agent_instructions",
    ),
    "crewai_flow": AdapterApplicabilityProfile(
        adapter_id="crewai_flow",
        request_read_status=ApplicabilityStatus.N_A_TIER2,
        capture_family=None,
        base_instruction_surface=None,
        reason="terminal flow adapter has no generic model-visible request surface",
        tier2_pointer="tests/e2e/adapters/test_crewai_flow.py",
    ),
}

_REQUEST_PROFILES = {
    adapter_id: AdapterApplicabilityProfile(
        adapter_id=profile.adapter_id,
        request_read_status=profile.request_read_status,
        capture_family=profile.capture_family,
        base_instruction_surface=profile.base_instruction_surface,
        reason=profile.reason,
        tier2_pointer=profile.tier2_pointer,
        covered_by_existing=dict(_GENERIC_COVERED_ROWS),
        coverage_evidence_ids=dict(_GENERIC_COVERAGE_EVIDENCE_IDS),
        scenario_overrides=profile.scenario_overrides,
    )
    for adapter_id, profile in _REQUEST_PROFILES.items()
}

_BINDINGS_BY_ADAPTER = {binding.adapter: binding for binding in INJECTION_BINDINGS}
DISPATCH_ADAPTER_IDS = frozenset(_BINDINGS_BY_ADAPTER)
BRIDGE_ADAPTER_IDS = frozenset(INJECTION_EXCLUDED_MODULES)
BASELINE_ADAPTER_IDS = frozenset(DISPATCH_ADAPTER_IDS | BRIDGE_ADAPTER_IDS)


def _dispatch_status(
    adapter_id: str,
    scenario: BaselineScenario,
) -> tuple[ApplicabilityStatus, str | None, str | None, tuple[CoverageEvidence, ...]]:
    binding = _BINDINGS_BY_ADAPTER[adapter_id]
    if binding.tier1_status is not Tier1Status.N_A_TIER2:
        return ApplicabilityStatus.APPLICABLE, None, None, ()

    reason = str(binding.na_subreason.value)
    return (
        ApplicabilityStatus.TIER2_BLOCKED,
        f"{reason}; no instrumented scenario-equivalent live evidence for {scenario.id}",
        None,
        (),
    )


def applicability_for(
    adapter_id: str,
    scenario: BaselineScenario,
) -> ApplicabilityCell:
    if adapter_id in BRIDGE_ADAPTER_IDS:
        return ApplicabilityCell(
            adapter_id=adapter_id,
            scenario_id=scenario.id,
            status=ApplicabilityStatus.EXCLUDED_BRIDGE,
            reason=_BRIDGE_REASON,
        )

    profile = _REQUEST_PROFILES.get(adapter_id)
    if profile is None:
        return ApplicabilityCell(
            adapter_id=adapter_id,
            scenario_id=scenario.id,
            status=ApplicabilityStatus.UNKNOWN_FAIL_CLOSED,
            reason="adapter has no reviewed baseline applicability profile",
        )

    if scenario.id in profile.covered_by_existing:
        return ApplicabilityCell(
            adapter_id=adapter_id,
            scenario_id=scenario.id,
            status=ApplicabilityStatus.COVERED_BY_EXISTING,
            covered_by_existing=profile.covered_by_existing[scenario.id],
            coverage_evidence=tuple(
                evidence_by_id(evidence_id)
                for evidence_id in profile.coverage_evidence_ids.get(scenario.id, ())
            ),
        )

    if scenario.id in profile.scenario_overrides:
        status = profile.scenario_overrides[scenario.id]
    elif scenario.kind is ScenarioKind.DISPATCH:
        status, reason, tier2_pointer, evidence = _dispatch_status(adapter_id, scenario)
        return ApplicabilityCell(
            adapter_id=adapter_id,
            scenario_id=scenario.id,
            status=status,
            reason=reason,
            tier2_pointer=tier2_pointer,
            coverage_evidence=evidence,
        )
    else:
        status = profile.request_read_status

    evidence = tuple(
        evidence_by_id(evidence_id)
        for evidence_id in profile.coverage_evidence_ids.get(scenario.id, ())
    )
    if status is ApplicabilityStatus.N_A_TIER2 and not evidence:
        return ApplicabilityCell(
            adapter_id=adapter_id,
            scenario_id=scenario.id,
            status=ApplicabilityStatus.TIER2_BLOCKED,
            capture_family=profile.capture_family,
            base_instruction_surface=profile.base_instruction_surface,
            reason=f"{profile.reason}; no scenario-equivalent coverage is registered",
        )

    return ApplicabilityCell(
        adapter_id=adapter_id,
        scenario_id=scenario.id,
        status=status,
        capture_family=profile.capture_family,
        base_instruction_surface=profile.base_instruction_surface,
        reason=profile.reason,
        tier2_pointer=profile.tier2_pointer,
        coverage_evidence=evidence,
    )


def build_applicability_matrix(
    adapter_ids: frozenset[str] = BASELINE_ADAPTER_IDS,
    scenarios: tuple[BaselineScenario, ...] = SCENARIOS,
) -> tuple[ApplicabilityCell, ...]:
    return tuple(
        applicability_for(adapter_id, scenario)
        for adapter_id in sorted(adapter_ids)
        for scenario in scenarios
    )


def unknown_fail_closed_cells(
    cells: tuple[ApplicabilityCell, ...] | None = None,
) -> tuple[ApplicabilityCell, ...]:
    inspected = cells if cells is not None else build_applicability_matrix()
    return tuple(
        cell
        for cell in inspected
        if cell.status is ApplicabilityStatus.UNKNOWN_FAIL_CLOSED
    )
