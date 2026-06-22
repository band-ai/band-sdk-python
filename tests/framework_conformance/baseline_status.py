"""Shared status and scenario types for baseline conformance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BaselineLevel(str, Enum):
    L0 = "l0"
    L1 = "l1"
    L2 = "l2"
    L3 = "l3"
    L4 = "l4"


class ProofTier(str, Enum):
    TIER1 = "tier1"
    TIER2 = "tier2"


class ScenarioKind(str, Enum):
    REQUEST_READ = "request_read"
    DISPATCH = "dispatch"
    RUNTIME_STATE = "runtime_state"
    LIVE_E2E = "live_e2e"


class SeamOwner(str, Enum):
    PREPROCESSOR = "preprocessor"
    ADAPTER_INPUT = "adapter_input"
    ADAPTER_PAYLOAD = "adapter_payload"
    MODEL_VISIBLE_REQUEST = "model_visible_request"
    DISPATCH_PATH = "dispatch_path"
    RUNTIME_STATE = "runtime_state"
    EXISTING_TEST = "existing_test"
    LIVE_PLATFORM = "live_platform"


class BaselineContract(str, Enum):
    L0_PLATFORM_ADAPTATION = "l0_platform_adaptation"
    L1_CUSTOMIZATION = "l1_customization"
    L2_CONTEXT_FIDELITY = "l2_context_fidelity"
    L3_MULTI_PARTICIPANT = "l3_multi_participant"
    L4_REHYDRATION = "l4_rehydration"
    SDK_HARDENING = "sdk_hardening"


class EvidenceOracle(str, Enum):
    TOOL_DISPATCH = "tool_dispatch"
    PLATFORM_MESSAGE_CREATED = "platform_message_created"
    PARTICIPANT_ADDED = "participant_added"
    PARTICIPANT_REMOVED = "participant_removed"
    PARTICIPANTS_LISTED = "participants_listed"
    PEERS_LISTED = "peers_listed"
    NO_WAKE = "no_wake"
    PENDING_WORK_ONCE = "pending_work_once"
    HANDLED_MESSAGE_NOT_REPRESENTED = "handled_message_not_represented"
    COMPLETED_TOOL_NOT_REQUEUED = "completed_tool_not_requeued"
    COLD_START_HISTORY_ORDER = "cold_start_history_order"


class EvidenceMetric(str, Enum):
    WALL_CLOCK = "wall_clock"
    LLM_CALL_COUNT = "llm_call_count"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    ESTIMATED_USD = "estimated_usd"
    HISTORY_REPLAY_TOKENS = "history_replay_tokens"
    NEW_INFERENCE_TOKENS = "new_inference_tokens"


class AdapterScope(str, Enum):
    DEFAULT_E2E_MATRIX = "default_e2e_matrix"
    RUNTIME_OWNED = "runtime_owned"
    ALL = "all"


class ScenarioStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    N_A_TIER2 = "n_a_tier2"
    EXCLUDED_BRIDGE = "excluded_bridge"
    UNKNOWN_FAIL_CLOSED = "unknown_fail_closed"
    TIER2_BLOCKED = "tier2_blocked"
    COVERED_BY_EXISTING = "covered_by_existing"


@dataclass(frozen=True, kw_only=True)
class CoveredByExisting:
    test_path: str
    test_names: tuple[str, ...]
    assertion_summary: str


@dataclass(frozen=True, kw_only=True)
class CoverageEvidence:
    evidence_id: str
    scenario_ids: frozenset[str]
    adapter_ids: frozenset[str] | AdapterScope
    proof_tier: ProofTier
    test_path: str
    test_names: tuple[str, ...]
    observed_oracles: frozenset[EvidenceOracle]
    assertion_summary: str
    observed_tools: frozenset[str] = frozenset()
    negative_oracles: frozenset[EvidenceOracle] = frozenset()
    metrics: frozenset[EvidenceMetric] = frozenset()
    equivalence_notes: str = ""


@dataclass(frozen=True, kw_only=True)
class BaselineResult:
    adapter_id: str
    scenario_id: str
    status: ScenarioStatus
    reason: str | None = None
    tier2_pointer: str | None = None
    covered_by_existing: CoveredByExisting | None = None
