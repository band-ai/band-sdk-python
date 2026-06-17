from __future__ import annotations

from tests.framework_conformance.baseline_status import (
    AdapterScope,
    CoverageEvidence,
    EvidenceOracle,
    ProofTier,
)

EVIDENCE: dict[str, CoverageEvidence] = {
    "runtime.no_wake_preprocessor": CoverageEvidence(
        evidence_id="runtime.no_wake_preprocessor",
        scenario_ids=frozenset({"L3.runtime.no_wake_non_messages"}),
        adapter_ids=AdapterScope.ALL,
        proof_tier=ProofTier.TIER1,
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
        observed_oracles=frozenset({EvidenceOracle.NO_WAKE}),
        assertion_summary=(
            "preprocessor tests cover non-message, self-authored, and unmentioned "
            "events not waking adapter handlers"
        ),
    ),
    "runtime.cleanup_crash_boundary": CoverageEvidence(
        evidence_id="runtime.cleanup_crash_boundary",
        scenario_ids=frozenset(
            {"L4.runtime.cleanup_not_required_for_crash_correctness"}
        ),
        adapter_ids=AdapterScope.ALL,
        proof_tier=ProofTier.TIER1,
        test_path="tests/runtime/test_execution.py",
        test_names=(
            "test_sync_processes_backlog_messages",
            "test_ws_replay_with_processed_metadata_is_not_reopened",
        ),
        observed_oracles=frozenset({EvidenceOracle.HANDLED_MESSAGE_NOT_REPRESENTED}),
        assertion_summary=(
            "runtime restart tests cover recovery without depending on adapter "
            "cleanup from the previous process"
        ),
    ),
}


def evidence_by_id(evidence_id: str) -> CoverageEvidence:
    return EVIDENCE[evidence_id]
