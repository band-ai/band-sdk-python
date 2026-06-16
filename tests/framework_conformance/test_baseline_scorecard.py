"""Meta-tests for the shared baseline conformance scorecard substrate."""

from __future__ import annotations

from collections import Counter, defaultdict
import os
from pathlib import Path
import re

import pytest

from tests.framework_conformance.baseline_applicability import (
    BASELINE_ADAPTER_IDS,
    BRIDGE_ADAPTER_IDS,
    ApplicabilityCell,
    ApplicabilityStatus,
    applicability_for,
    build_applicability_matrix,
    unknown_fail_closed_cells,
)
from tests.framework_conformance.baseline_scenarios import SCENARIOS, SCENARIOS_BY_ID
from tests.framework_conformance.baseline_status import (
    AdapterScope,
    BaselineContract,
    BaselineLevel,
    EvidenceMetric,
    ProofTier,
    ScenarioKind,
    ScenarioStatus,
    SeamOwner,
)
from tests.framework_conformance.injection_registry import (
    INJECTION_BINDINGS,
    INJECTION_EXCLUDED_MODULES,
    Family,
)
from tests.framework_conformance.request_capture import (
    REQUEST_CAPTURE_PROBES,
    SENTINEL_OPENAI_API_KEY,
    tier1_sentinel_provider_env,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HARNESS_ROOT = _REPO_ROOT / "tests" / "framework_conformance"
_ISSUE_ID_PATTERN = re.compile(r"\b" + "IN" + "T" + r"-\d+\b|" + "Lin" + "ear")
_RUNTIME_OWNED_ADAPTER_IDS = frozenset(
    binding.adapter
    for binding in INJECTION_BINDINGS
    if binding.family is Family.RUNTIME_OWNED_ROUTING
)


def test_scenarios_have_stable_domain_metadata() -> None:
    assert SCENARIOS
    assert len(SCENARIOS_BY_ID) == len(SCENARIOS)

    for scenario in SCENARIOS:
        assert scenario.id == scenario.id.strip()
        assert scenario.id.startswith(scenario.level.name)
        assert scenario.proof_tier is ProofTier.TIER1
        assert isinstance(scenario.kind, ScenarioKind)
        assert isinstance(scenario.seam_owner, SeamOwner)
        assert isinstance(scenario.domain_contract, BaselineContract)
        assert isinstance(scenario.core_contract, bool)
        assert scenario.title
        if scenario.kind is ScenarioKind.DISPATCH:
            assert scenario.applies_to_dispatch_bindings
        if scenario.kind is ScenarioKind.REQUEST_READ:
            assert scenario.requires_request_capture
        assert scenario.required_oracles == frozenset(scenario.required_oracles)
        assert scenario.required_tools == frozenset(scenario.required_tools)
        assert scenario.required_metrics == frozenset(scenario.required_metrics)


def test_scenario_counts_by_contract_and_classification_are_intentional() -> None:
    by_contract = Counter(s.domain_contract for s in SCENARIOS)
    by_classification = Counter(
        "core" if s.core_contract else "hardening" for s in SCENARIOS
    )

    assert by_contract == {
        BaselineContract.L0_PLATFORM_ADAPTATION: 8,
        BaselineContract.L1_CUSTOMIZATION: 3,
        BaselineContract.L2_CONTEXT_FIDELITY: 4,
        BaselineContract.L3_MULTI_PARTICIPANT: 3,
        BaselineContract.L4_REHYDRATION: 4,
        BaselineContract.SDK_HARDENING: 2,
    }
    assert by_classification == {"core": 22, "hardening": 2}


def test_result_statuses_cover_all_review_outcomes() -> None:
    assert {status.value for status in ScenarioStatus} == {
        "pass",
        "fail",
        "n_a_tier2",
        "excluded_bridge",
        "unknown_fail_closed",
        "tier2_blocked",
        "covered_by_existing",
    }


def test_harness_code_uses_domain_contracts_not_issue_ids() -> None:
    offenders: list[str] = []
    for path in _HARNESS_ROOT.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        if _ISSUE_ID_PATTERN.search(text):
            offenders.append(str(path.relative_to(_REPO_ROOT)))

    assert not offenders


def test_tier1_provider_env_forces_sentinel_key_and_clears_base_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-real-looking-value")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://live-provider.invalid/v1")
    monkeypatch.setenv("OPENAI_API_BASE", "https://legacy-provider.invalid/v1")
    monkeypatch.setenv("OPENAI_API_HOST", "https://host-provider.invalid")

    with tier1_sentinel_provider_env():
        assert os.environ["OPENAI_API_KEY"] == SENTINEL_OPENAI_API_KEY
        assert "OPENAI_BASE_URL" not in os.environ
        assert "OPENAI_API_BASE" not in os.environ
        assert "OPENAI_API_HOST" not in os.environ

    assert os.environ["OPENAI_API_KEY"] == "sk-proj-real-looking-value"
    assert os.environ["OPENAI_BASE_URL"] == "https://live-provider.invalid/v1"
    assert os.environ["OPENAI_API_BASE"] == "https://legacy-provider.invalid/v1"
    assert os.environ["OPENAI_API_HOST"] == "https://host-provider.invalid"


def test_scorecard_is_seeded_from_static_registries() -> None:
    expected = {binding.adapter for binding in INJECTION_BINDINGS} | set(
        INJECTION_EXCLUDED_MODULES
    )

    assert BASELINE_ADAPTER_IDS == expected


def test_every_adapter_scenario_pair_has_reviewed_applicability() -> None:
    cells = build_applicability_matrix()
    expected_count = len(BASELINE_ADAPTER_IDS) * len(SCENARIOS)

    assert len(cells) == expected_count
    assert unknown_fail_closed_cells(cells) == ()


def test_unknown_fail_closed_is_never_a_passing_status() -> None:
    synthetic = ApplicabilityCell(
        adapter_id="new_adapter",
        scenario_id="L0.request.platform_context",
        status=ApplicabilityStatus.UNKNOWN_FAIL_CLOSED,
        reason="unreviewed adapter/scenario pair",
    )

    assert synthetic.status is ApplicabilityStatus.UNKNOWN_FAIL_CLOSED
    assert synthetic.status is not ApplicabilityStatus.APPLICABLE
    assert synthetic.status is not ApplicabilityStatus.COVERED_BY_EXISTING
    assert unknown_fail_closed_cells((synthetic,)) == (synthetic,)


def _evidence_reaches_adapter(evidence, adapter_id: str) -> bool:
    if evidence.adapter_ids is AdapterScope.ALL:
        return True
    if evidence.adapter_ids is AdapterScope.RUNTIME_OWNED:
        return adapter_id in _RUNTIME_OWNED_ADAPTER_IDS
    return adapter_id in evidence.adapter_ids


def _validate_evidence(cell: ApplicabilityCell) -> None:
    scenario = SCENARIOS_BY_ID[cell.scenario_id]
    assert cell.coverage_evidence, (
        f"{cell.adapter_id} {cell.scenario_id} has no evidence"
    )

    observed_oracles = frozenset().union(
        *(evidence.observed_oracles for evidence in cell.coverage_evidence)
    )
    observed_tools = frozenset().union(
        *(evidence.observed_tools for evidence in cell.coverage_evidence)
    )
    observed_metrics = frozenset().union(
        *(evidence.metrics for evidence in cell.coverage_evidence)
    )
    assert scenario.required_oracles <= observed_oracles
    assert scenario.required_tools <= observed_tools
    assert scenario.required_metrics <= observed_metrics

    for evidence in cell.coverage_evidence:
        assert cell.scenario_id in evidence.scenario_ids
        assert _evidence_reaches_adapter(evidence, cell.adapter_id)
        assert evidence.assertion_summary
        assert evidence.test_names
        test_file = _REPO_ROOT / evidence.test_path
        assert test_file.is_file()
        text = test_file.read_text(encoding="utf-8")
        for test_name in evidence.test_names:
            assert re.search(rf"def {re.escape(test_name)}\(", text), (
                f"{cell.scenario_id} claims coverage by {test_name} "
                f"but {evidence.test_path} has no such test"
            )
        if evidence.proof_tier is ProofTier.TIER2:
            assert {
                EvidenceMetric.WALL_CLOCK,
                EvidenceMetric.LLM_CALL_COUNT,
                EvidenceMetric.INPUT_TOKENS,
                EvidenceMetric.OUTPUT_TOKENS,
                EvidenceMetric.ESTIMATED_USD,
            } <= evidence.metrics


def test_covered_by_existing_rows_have_real_test_paths_and_assertion_summary() -> None:
    covered = [
        cell
        for cell in build_applicability_matrix()
        if cell.status is ApplicabilityStatus.COVERED_BY_EXISTING
    ]

    assert covered
    file_text_cache: dict[str, str] = {}
    for cell in covered:
        coverage = cell.covered_by_existing
        assert coverage is not None
        test_file = _REPO_ROOT / coverage.test_path
        assert test_file.is_file()
        assert coverage.assertion_summary
        assert coverage.test_names
        _validate_evidence(cell)

        if coverage.test_path not in file_text_cache:
            file_text_cache[coverage.test_path] = test_file.read_text(encoding="utf-8")
        text = file_text_cache[coverage.test_path]
        for test_name in coverage.test_names:
            assert re.search(rf"def {re.escape(test_name)}\(", text), (
                f"{cell.scenario_id} claims coverage by {test_name} "
                f"but {coverage.test_path} has no such test"
            )


def test_protocol_bridges_are_excluded_from_ordinary_baseline_rows() -> None:
    assert BRIDGE_ADAPTER_IDS == frozenset({"a2a", "a2a_gateway", "acp"})

    for adapter_id in BRIDGE_ADAPTER_IDS:
        for scenario in SCENARIOS:
            cell = applicability_for(adapter_id, scenario)
            assert cell.status is ApplicabilityStatus.EXCLUDED_BRIDGE
            assert cell.reason


def test_applicable_request_rows_have_registered_matching_capture_probe() -> None:
    applicable_request_adapters: set[str] = set()
    for cell in build_applicability_matrix():
        scenario = SCENARIOS_BY_ID[cell.scenario_id]
        if scenario.kind is not ScenarioKind.REQUEST_READ:
            continue
        if cell.status is not ApplicabilityStatus.APPLICABLE:
            continue

        applicable_request_adapters.add(cell.adapter_id)
        assert cell.capture_family
        assert cell.base_instruction_surface
        probe = REQUEST_CAPTURE_PROBES.get(cell.adapter_id)
        assert probe is not None, (
            f"{cell.adapter_id} is APPLICABLE for {cell.scenario_id} "
            "but has no request-capture probe"
        )
        assert probe.family == cell.capture_family

    assert set(REQUEST_CAPTURE_PROBES) == applicable_request_adapters


def test_all_tier2_na_rows_have_scenario_equivalent_evidence() -> None:
    na_cells = [
        cell
        for cell in build_applicability_matrix()
        if cell.status is ApplicabilityStatus.N_A_TIER2
    ]

    for cell in na_cells:
        assert cell.reason
        assert cell.tier2_pointer
        assert (_REPO_ROOT / cell.tier2_pointer).is_file()
        _validate_evidence(cell)


def test_tier2_blocked_rows_do_not_claim_live_pointer_credit() -> None:
    blocked = [
        cell
        for cell in build_applicability_matrix()
        if cell.status is ApplicabilityStatus.TIER2_BLOCKED
    ]

    assert blocked
    assert all(cell.reason for cell in blocked)
    assert all(not cell.tier2_pointer for cell in blocked)
    assert all(not cell.coverage_evidence for cell in blocked)


def test_crewai_flow_l0_request_rows_stay_blocked_not_smoke_covered() -> None:
    l0_request_cells = [
        cell
        for cell in build_applicability_matrix()
        if cell.adapter_id == "crewai_flow"
        and cell.scenario_id.startswith("L0.request.")
    ]

    assert l0_request_cells
    assert all(
        cell.status is ApplicabilityStatus.TIER2_BLOCKED for cell in l0_request_cells
    )
    assert all(
        "no scenario-equivalent coverage" in str(cell.reason)
        for cell in l0_request_cells
    )
    assert all(not cell.coverage_evidence for cell in l0_request_cells)
    assert all(not cell.covered_by_existing for cell in l0_request_cells)


def test_dispatch_na_rows_have_e2e_pointer_and_honest_rows_are_applicable() -> None:
    cells_by_adapter: dict[str, list[ApplicabilityCell]] = defaultdict(list)
    for cell in build_applicability_matrix():
        scenario = SCENARIOS_BY_ID[cell.scenario_id]
        if scenario.kind is ScenarioKind.DISPATCH:
            cells_by_adapter[cell.adapter_id].append(cell)

    binding_by_adapter = {binding.adapter: binding for binding in INJECTION_BINDINGS}
    for adapter_id, cells in cells_by_adapter.items():
        if adapter_id in BRIDGE_ADAPTER_IDS:
            assert all(
                cell.status is ApplicabilityStatus.EXCLUDED_BRIDGE for cell in cells
            )
            continue

        binding = binding_by_adapter[adapter_id]
        if binding.tier1_status.value == "n_a_tier2":
            assert all(
                cell.status is ApplicabilityStatus.TIER2_BLOCKED for cell in cells
            )
        else:
            assert all(cell.status is ApplicabilityStatus.APPLICABLE for cell in cells)


def test_level_distribution_is_complete() -> None:
    by_level = Counter(scenario.level for scenario in SCENARIOS)

    assert by_level == {
        BaselineLevel.L0: 8,
        BaselineLevel.L1: 3,
        BaselineLevel.L2: 4,
        BaselineLevel.L3: 4,
        BaselineLevel.L4: 5,
    }


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_request_capture_flag_matches_request_rows(scenario) -> None:
    assert scenario.requires_request_capture is (
        scenario.kind is ScenarioKind.REQUEST_READ
    )
