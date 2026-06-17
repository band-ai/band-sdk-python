"""Static L0-L4 baseline conformance scenario registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from tests.framework_conformance.baseline_status import (
    BaselineContract,
    BaselineLevel,
    EvidenceMetric,
    EvidenceOracle,
    ProofTier,
    ScenarioKind,
    SeamOwner,
)


@dataclass(frozen=True, kw_only=True)
class BaselineScenario:
    id: str
    level: BaselineLevel
    proof_tier: ProofTier
    kind: ScenarioKind
    seam_owner: SeamOwner
    domain_contract: BaselineContract
    title: str
    core_contract: bool = True
    applies_to_dispatch_bindings: bool = False
    requires_request_capture: bool = False
    required_oracles: frozenset[EvidenceOracle] = frozenset()
    required_tools: frozenset[str] = frozenset()
    required_metrics: frozenset[EvidenceMetric] = frozenset()


def tier1(
    *,
    id: str,
    level: BaselineLevel,
    kind: ScenarioKind,
    seam_owner: SeamOwner,
    title: str,
    domain_contract: BaselineContract,
    core_contract: bool = True,
    applies_to_dispatch_bindings: bool = False,
    requires_request_capture: bool = False,
    required_oracles: frozenset[EvidenceOracle] = frozenset(),
    required_tools: frozenset[str] = frozenset(),
    required_metrics: frozenset[EvidenceMetric] = frozenset(),
) -> BaselineScenario:
    return BaselineScenario(
        id=id,
        level=level,
        proof_tier=ProofTier.TIER1,
        kind=kind,
        seam_owner=seam_owner,
        domain_contract=domain_contract,
        title=title,
        core_contract=core_contract,
        applies_to_dispatch_bindings=applies_to_dispatch_bindings,
        requires_request_capture=requires_request_capture,
        required_oracles=required_oracles,
        required_tools=required_tools,
        required_metrics=required_metrics,
    )


SCENARIOS: tuple[BaselineScenario, ...] = (
    tier1(
        id="L0.request.platform_context",
        level=BaselineLevel.L0,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_INPUT,
        title="platform identity and base instructions reach model-visible input",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        requires_request_capture=True,
    ),
    tier1(
        id="L0.request.history",
        level=BaselineLevel.L0,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.PREPROCESSOR,
        title="history plus current trigger are assembled without duplication",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        requires_request_capture=True,
    ),
    tier1(
        id="L0.request.participants",
        level=BaselineLevel.L0,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_INPUT,
        title="participant-change roster is delivered with current participants",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        requires_request_capture=True,
    ),
    tier1(
        id="L0.dispatch.send_message",
        level=BaselineLevel.L0,
        kind=ScenarioKind.DISPATCH,
        seam_owner=SeamOwner.DISPATCH_PATH,
        title="platform send-message tool dispatch reaches recorder",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        applies_to_dispatch_bindings=True,
        required_oracles=frozenset(
            {EvidenceOracle.TOOL_DISPATCH, EvidenceOracle.PLATFORM_MESSAGE_CREATED}
        ),
        required_tools=frozenset({"thenvoi_send_message"}),
    ),
    tier1(
        id="L0.dispatch.add_participant",
        level=BaselineLevel.L0,
        kind=ScenarioKind.DISPATCH,
        seam_owner=SeamOwner.DISPATCH_PATH,
        title="platform add-participant tool dispatch reaches recorder",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        applies_to_dispatch_bindings=True,
        required_oracles=frozenset(
            {EvidenceOracle.TOOL_DISPATCH, EvidenceOracle.PARTICIPANT_ADDED}
        ),
        required_tools=frozenset({"thenvoi_add_participant"}),
    ),
    tier1(
        id="L0.dispatch.remove_participant",
        level=BaselineLevel.L0,
        kind=ScenarioKind.DISPATCH,
        seam_owner=SeamOwner.DISPATCH_PATH,
        title="platform remove-participant tool dispatch reaches recorder",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        applies_to_dispatch_bindings=True,
        required_oracles=frozenset(
            {EvidenceOracle.TOOL_DISPATCH, EvidenceOracle.PARTICIPANT_REMOVED}
        ),
        required_tools=frozenset({"thenvoi_remove_participant"}),
    ),
    tier1(
        id="L0.dispatch.get_participants",
        level=BaselineLevel.L0,
        kind=ScenarioKind.DISPATCH,
        seam_owner=SeamOwner.DISPATCH_PATH,
        title="platform get-participants tool dispatch reaches recorder",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        applies_to_dispatch_bindings=True,
        required_oracles=frozenset(
            {EvidenceOracle.TOOL_DISPATCH, EvidenceOracle.PARTICIPANTS_LISTED}
        ),
        required_tools=frozenset({"thenvoi_get_participants"}),
    ),
    tier1(
        id="L0.dispatch.lookup_peers",
        level=BaselineLevel.L0,
        kind=ScenarioKind.DISPATCH,
        seam_owner=SeamOwner.DISPATCH_PATH,
        title="platform lookup-peers tool dispatch reaches recorder",
        domain_contract=BaselineContract.L0_PLATFORM_ADAPTATION,
        applies_to_dispatch_bindings=True,
        required_oracles=frozenset(
            {EvidenceOracle.TOOL_DISPATCH, EvidenceOracle.PEERS_LISTED}
        ),
        required_tools=frozenset({"thenvoi_lookup_peers"}),
    ),
    tier1(
        id="L1.request.custom_prompt_present",
        level=BaselineLevel.L1,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="custom prompt text reaches model-visible request",
        domain_contract=BaselineContract.L1_CUSTOMIZATION,
        requires_request_capture=True,
    ),
    tier1(
        id="L1.request.custom_prompt_additive",
        level=BaselineLevel.L1,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="custom prompt is additive with platform base instructions by default",
        domain_contract=BaselineContract.L1_CUSTOMIZATION,
        requires_request_capture=True,
    ),
    tier1(
        id="L1.dispatch.custom_tool",
        level=BaselineLevel.L1,
        kind=ScenarioKind.DISPATCH,
        seam_owner=SeamOwner.DISPATCH_PATH,
        title="developer custom-tool dispatch reaches handler path",
        domain_contract=BaselineContract.L1_CUSTOMIZATION,
        applies_to_dispatch_bindings=True,
        required_oracles=frozenset({EvidenceOracle.TOOL_DISPATCH}),
        required_tools=frozenset({"log_keyword"}),
    ),
    tier1(
        id="L2.request.full_history",
        level=BaselineLevel.L2,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="full supplied history reaches model-visible request",
        domain_contract=BaselineContract.L2_CONTEXT_FIDELITY,
        requires_request_capture=True,
    ),
    tier1(
        id="L2.request.earliest_turn",
        level=BaselineLevel.L2,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="earliest turn survives longer-history fixture",
        domain_contract=BaselineContract.L2_CONTEXT_FIDELITY,
        requires_request_capture=True,
    ),
    tier1(
        id="L2.request.chronological_order",
        level=BaselineLevel.L2,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="history is chronological with current trigger last",
        domain_contract=BaselineContract.L2_CONTEXT_FIDELITY,
        requires_request_capture=True,
    ),
    tier1(
        id="L2.request.speaker_attribution",
        level=BaselineLevel.L2,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="own turns and peer/user turns remain distinguishable",
        domain_contract=BaselineContract.L2_CONTEXT_FIDELITY,
        requires_request_capture=True,
    ),
    tier1(
        id="L3.request.roster_handles",
        level=BaselineLevel.L3,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_INPUT,
        title="participant-change roster contains handles and participant types",
        domain_contract=BaselineContract.L3_MULTI_PARTICIPANT,
        requires_request_capture=True,
    ),
    tier1(
        id="L3.request.mention_convention",
        level=BaselineLevel.L3,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_INPUT,
        title="routing instructions use concrete handle mentions",
        domain_contract=BaselineContract.L3_MULTI_PARTICIPANT,
        requires_request_capture=True,
    ),
    tier1(
        id="L3.request.multi_author_history",
        level=BaselineLevel.L3,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="multi-author history preserves order and attribution",
        domain_contract=BaselineContract.L3_MULTI_PARTICIPANT,
        requires_request_capture=True,
    ),
    tier1(
        id="L3.runtime.no_wake_non_messages",
        level=BaselineLevel.L3,
        kind=ScenarioKind.RUNTIME_STATE,
        seam_owner=SeamOwner.EXISTING_TEST,
        title="non-message and unmentioned events do not wake adapters",
        domain_contract=BaselineContract.SDK_HARDENING,
        core_contract=False,
        required_oracles=frozenset({EvidenceOracle.NO_WAKE}),
    ),
    tier1(
        id="L4.request.cold_start_history",
        level=BaselineLevel.L4,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.ADAPTER_PAYLOAD,
        title="cold-start request rebuilds persisted conversation order",
        domain_contract=BaselineContract.L4_REHYDRATION,
        requires_request_capture=True,
        required_oracles=frozenset({EvidenceOracle.COLD_START_HISTORY_ORDER}),
    ),
    tier1(
        id="L4.request.offline_pending_once",
        level=BaselineLevel.L4,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.MODEL_VISIBLE_REQUEST,
        title="offline pending message is presented exactly once as current work",
        domain_contract=BaselineContract.L4_REHYDRATION,
        requires_request_capture=True,
        required_oracles=frozenset({EvidenceOracle.PENDING_WORK_ONCE}),
    ),
    tier1(
        id="L4.request.handled_message_dedup",
        level=BaselineLevel.L4,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.MODEL_VISIBLE_REQUEST,
        title="already handled messages are not re-presented as new work",
        domain_contract=BaselineContract.L4_REHYDRATION,
        requires_request_capture=True,
        required_oracles=frozenset({EvidenceOracle.HANDLED_MESSAGE_NOT_REPRESENTED}),
    ),
    tier1(
        id="L4.request.completed_tool_no_requeue",
        level=BaselineLevel.L4,
        kind=ScenarioKind.REQUEST_READ,
        seam_owner=SeamOwner.MODEL_VISIBLE_REQUEST,
        title="completed tool calls restore as history without re-running side effects",
        domain_contract=BaselineContract.L4_REHYDRATION,
        requires_request_capture=True,
        required_oracles=frozenset({EvidenceOracle.COMPLETED_TOOL_NOT_REQUEUED}),
    ),
    tier1(
        id="L4.runtime.cleanup_not_required_for_crash_correctness",
        level=BaselineLevel.L4,
        kind=ScenarioKind.RUNTIME_STATE,
        seam_owner=SeamOwner.RUNTIME_STATE,
        title="crash recovery correctness does not depend on graceful cleanup",
        domain_contract=BaselineContract.SDK_HARDENING,
        core_contract=False,
        required_oracles=frozenset({EvidenceOracle.HANDLED_MESSAGE_NOT_REPRESENTED}),
    ),
)

SCENARIOS_BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


def scenarios_for_contract(
    contract: BaselineContract,
) -> tuple[BaselineScenario, ...]:
    return tuple(s for s in SCENARIOS if s.domain_contract is contract)


def scenario_ids(scenarios: Iterable[BaselineScenario] = SCENARIOS) -> frozenset[str]:
    return frozenset(s.id for s in scenarios)
