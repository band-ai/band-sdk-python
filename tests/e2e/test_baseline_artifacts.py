from __future__ import annotations

import json

import pytest

from band.core.simple_adapter import ProviderUsageSnapshot, SimpleAdapter
from tests.e2e.baseline_artifacts import (
    BaselinePricing,
    BaselineProviderUsage,
    BaselineTier2Timer,
    aggregate_provider_usage,
    baseline_pricing_from_env,
    estimate_tokens,
    l0_usage_from_live_observation,
    provider_usage_blocked_reason,
    provider_usage_from_adapter,
    write_baseline_tier2_artifact,
    write_baseline_tier2_blocked_artifact,
    write_provider_usage_blocked_artifact_if_needed,
)


class _UsageAdapter(SimpleAdapter[object]):
    async def on_message(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise NotImplementedError


def _timer() -> BaselineTier2Timer:
    return BaselineTier2Timer(started_at="2026-06-10T00:00:00+00:00", start_monotonic=0)


def _pricing() -> BaselinePricing:
    return BaselinePricing(
        input_usd_per_million_tokens=1.0,
        output_usd_per_million_tokens=2.0,
        source="test",
    )


def _usage() -> BaselineProviderUsage:
    return BaselineProviderUsage(
        api_call_count=2,
        input_tokens=100,
        output_tokens=25,
        total_tokens=125,
        source="provider.test.usage",
        raw_snapshots=[{"source": "provider.test.usage"}],
    )


def _row_evidence(
    scenario_ref: str = "L4.request.cold_start_history",
) -> dict[str, object]:
    return {scenario_ref: {"reply_count": 1}}


def _row_observation(
    scenario_ref: str = "L4.request.cold_start_history",
) -> dict[str, str]:
    return {
        "kind": "message",
        "id": "msg-1",
        "assertion": "reply observed",
        "scenario_ref": scenario_ref,
    }


def _l1_evidence() -> dict[str, object]:
    return {
        "L1.request.custom_prompt_present": {
            "custom_prompt_marker": "SNOLLYGOSTER",
            "custom_prompt_marker_seen_in_steps": ["custom_tool", "room_listing"],
        },
        "L1.request.custom_prompt_additive": {
            "platform_live_user_seen": True,
            "platform_non_participant_absent": True,
            "platform_observation_source": "live_room_answer",
        },
        "L1.dispatch.custom_tool": {
            "custom_tool_name": "log_keyword",
            "custom_tool_args": {"message": "M1_PROBE"},
            "custom_tool_calls": 1,
            "custom_tool_return_seen": True,
        },
    }


def test_estimate_tokens_is_deterministic_and_nonzero_for_non_proof_metadata() -> None:
    assert estimate_tokens(["abcd"]) == 1
    assert estimate_tokens(["abcde"]) == 2
    assert estimate_tokens([]) == 0


def test_baseline_pricing_from_env_fails_closed_without_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS", raising=False)
    monkeypatch.delenv("E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS", raising=False)
    monkeypatch.delenv("E2E_BASELINE_PRICING_SOURCE", raising=False)

    with pytest.raises(AssertionError, match="tier2_blocked: missing cost metadata"):
        baseline_pricing_from_env()


def test_baseline_pricing_from_env_fails_closed_for_invalid_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS", "nan")
    monkeypatch.setenv("E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS", "0")
    monkeypatch.setenv("E2E_BASELINE_PRICING_SOURCE", "test")

    with pytest.raises(AssertionError, match="tier2_blocked: invalid cost metadata"):
        baseline_pricing_from_env()


def test_baseline_pricing_from_env_fails_closed_for_malformed_rates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS", "not-a-float")
    monkeypatch.setenv("E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS", "2")
    monkeypatch.setenv("E2E_BASELINE_PRICING_SOURCE", "test")

    with pytest.raises(AssertionError, match="tier2_blocked: invalid cost metadata"):
        baseline_pricing_from_env()


def test_provider_usage_blocked_reason_is_explicit_for_unsupported_adapters() -> None:
    assert provider_usage_blocked_reason("codex") == (
        "tier2_blocked: adapter does not expose provider-owned input/output token "
        "usage for baseline cost proof: codex"
    )
    assert provider_usage_blocked_reason("parlant") == (
        "tier2_blocked: Parlant baseline proof requires a dedicated in-process "
        "Parlant server runner and does not expose provider-owned input/output "
        "token usage for baseline cost proof"
    )
    assert provider_usage_blocked_reason("anthropic") is None
    assert provider_usage_blocked_reason("claude_sdk") is None


def test_write_provider_usage_blocked_artifact_if_needed_writes_unsupported_row(
    tmp_path,
) -> None:
    reason = write_provider_usage_blocked_artifact_if_needed(
        scenario_id="L2.request.full_history",
        scenario_refs=["L2.request.full_history", "L2.request.earliest_turn"],
        adapter="codex",
        artifact_dir=tmp_path,
    )

    assert reason == provider_usage_blocked_reason("codex")
    [path] = tmp_path.glob("*-blocked.json")
    artifact = json.loads(path.read_text())
    assert artifact["status"] == "TIER2_BLOCKED"
    assert artifact["adapter"] == "codex"
    assert artifact["blocked_scenarios"] == {
        "L2.request.earliest_turn": reason,
        "L2.request.full_history": reason,
    }


def test_write_provider_usage_blocked_artifact_if_needed_returns_none_for_supported(
    tmp_path,
) -> None:
    assert (
        write_provider_usage_blocked_artifact_if_needed(
            scenario_id="L2.request.full_history",
            scenario_refs=["L2.request.full_history"],
            adapter="anthropic",
            artifact_dir=tmp_path,
        )
        is None
    )
    assert list(tmp_path.glob("*.json")) == []


def test_provider_usage_from_adapter_requires_recorded_usage() -> None:
    with pytest.raises(
        AssertionError, match="adapter did not record provider-owned usage"
    ):
        provider_usage_from_adapter(_UsageAdapter(), adapter_name="anthropic")


def test_l0_usage_from_live_observation_falls_back_to_text_estimate() -> None:
    usage = l0_usage_from_live_observation(
        adapter=_UsageAdapter(),
        adapter_name="codex",
        input_texts=["hello"],
        output_texts=["world"],
        observed_agent_text_message_count=3,
    )

    assert usage.api_call_count == 3
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
    assert (
        usage.source
        == "l0_deterministic_text_estimate_provider_usage_unavailable:codex"
    )
    assert usage.raw_snapshots == []


def test_l0_usage_from_live_observation_prefers_provider_usage() -> None:
    adapter = _UsageAdapter()
    adapter._record_provider_usage(
        source="provider.test.usage",
        input_tokens=10,
        output_tokens=5,
    )

    usage = l0_usage_from_live_observation(
        adapter=adapter,
        adapter_name="anthropic",
        input_texts=["ignored"],
        output_texts=["ignored"],
        observed_agent_text_message_count=3,
    )

    assert usage.source == "provider.test.usage"
    assert usage.raw_snapshots


def test_write_l0_artifact_marks_text_estimate_token_source(tmp_path) -> None:
    usage = BaselineProviderUsage(
        api_call_count=1,
        input_tokens=2,
        output_tokens=2,
        total_tokens=4,
        source="l0_deterministic_text_estimate_provider_usage_unavailable:codex",
        raw_snapshots=[],
    )

    path = write_baseline_tier2_artifact(
        scenario_id="L0.request.platform_context",
        scenario_refs=["L0.request.platform_context"],
        adapter="codex",
        timer=_timer(),
        pricing=_pricing(),
        provider_usage=usage,
        input_texts=["prompt"],
        output_texts=["answer"],
        observed_agent_text_message_count=1,
        evidence={"L0.request.platform_context": {"identity_reply_count": 1}},
        platform_observations=[_row_observation("L0.request.platform_context")],
        artifact_dir=tmp_path,
    )

    artifact = json.loads(path.read_text())
    assert (
        artifact["token_count_source"]
        == "l0_deterministic_text_estimate_provider_usage_unavailable"
    )
    assert artifact["llm_api_call_count_source"] == usage.source


def test_aggregate_provider_usage_sums_provider_snapshots() -> None:
    usage = aggregate_provider_usage(
        [
            ProviderUsageSnapshot(
                source="provider.a",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                raw={"id": "a"},
            ),
            ProviderUsageSnapshot(
                source="provider.a",
                input_tokens=20,
                output_tokens=7,
                total_tokens=27,
                api_call_count=2,
                raw={"id": "b"},
            ),
        ]
    )

    assert usage.api_call_count == 3
    assert usage.input_tokens == 30
    assert usage.output_tokens == 12
    assert usage.total_tokens == 42
    assert usage.source == "provider.a"
    assert usage.raw_snapshots[0]["raw"] == {"id": "a"}


def test_aggregate_provider_usage_only_uses_cost_when_all_snapshots_have_cost() -> None:
    complete_cost = aggregate_provider_usage(
        [
            ProviderUsageSnapshot(
                source="provider.a",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cost_usd=0.01,
            ),
            ProviderUsageSnapshot(
                source="provider.a",
                input_tokens=20,
                output_tokens=7,
                total_tokens=27,
                cost_usd=0.02,
            ),
        ]
    )
    mixed_cost = aggregate_provider_usage(
        [
            ProviderUsageSnapshot(
                source="provider.a",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                cost_usd=0.01,
            ),
            ProviderUsageSnapshot(
                source="provider.a",
                input_tokens=20,
                output_tokens=7,
                total_tokens=27,
            ),
        ]
    )

    assert complete_cost.cost_usd == 0.03
    assert mixed_cost.cost_usd is None


def test_write_baseline_tier2_artifact_rejects_unregistered_scenarios(tmp_path) -> None:
    with pytest.raises(AssertionError, match="unknown baseline scenario id"):
        write_baseline_tier2_artifact(
            scenario_id="L4.live.restart",
            scenario_refs=["L4.request.cold_start_history"],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence={},
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_records_provider_usage_contract(
    tmp_path,
) -> None:
    path = write_baseline_tier2_artifact(
        scenario_id="L4.request.cold_start_history",
        scenario_refs=["L4.request.cold_start_history"],
        adapter="anthropic",
        timer=_timer(),
        pricing=_pricing(),
        provider_usage=_usage(),
        input_texts=[
            "history replay text",
            "OPENAI_API_KEY=sk-proj-secretsecretsecret new prompt",
        ],
        output_texts=["answer from thnv_a_secretsecretsecret"],
        observed_agent_text_message_count=1,
        evidence=_row_evidence(),
        platform_observations=[_row_observation()],
        l4_provider_token_split={
            "history_replay_tokens": 70,
            "new_inference_tokens": 15,
            "history_to_new_token_ratio": 70 / 15,
        },
        artifact_dir=tmp_path,
    )

    artifact = json.loads(path.read_text())
    assert artifact["schema_version"] == 1
    assert artifact["scenario_id"] == "L4.request.cold_start_history"
    assert artifact["scenario_refs"] == ["L4.request.cold_start_history"]
    assert artifact["adapter"] == "anthropic"
    assert artifact["level"] == "L4"
    assert artifact["scenario"] == "L4.request.cold_start_history"
    assert artifact["wall_clock_ms"] >= 0
    assert artifact["observed_agent_text_message_count"] == 1
    assert artifact["llm_api_call_count"] == 2
    assert artifact["llm_api_call_count_source"] == "provider.test.usage"
    assert artifact["input_tokens"] == 100
    assert artifact["output_tokens"] == 25
    assert artifact["total_tokens"] == 125
    assert artifact["token_count_source"] == "provider_reported_usage"
    assert artifact["estimated_usd"] == 0.00015
    assert artifact["provider_usage"] == {
        "source": "provider.test.usage",
        "raw_snapshots": [{"source": "provider.test.usage"}],
    }
    assert artifact["input_texts"] == [
        "history replay text",
        "OPENAI_API_KEY=<redacted> new prompt",
    ]
    assert artifact["output_texts"] == ["answer from <redacted>"]
    assert artifact["platform_observations"] == [_row_observation()]
    assert artifact["evidence"] == _row_evidence()
    assert artifact["l4_provider_token_split"] == {
        "history_replay_tokens": 70,
        "new_inference_tokens": 15,
        "history_to_new_token_ratio": 70 / 15,
        "source": "provider_reported_first_post_restart_call_proxy",
    }


def test_write_baseline_tier2_artifact_records_l4_multi_row_evidence(tmp_path) -> None:
    scenario_refs = [
        "L4.request.cold_start_history",
        "L4.request.offline_pending_once",
        "L4.request.handled_message_dedup",
        "L4.request.completed_tool_no_requeue",
    ]
    evidence = {scenario_ref: {"reply_count": 1} for scenario_ref in scenario_refs}

    path = write_baseline_tier2_artifact(
        scenario_id="L4.request.cold_start_history",
        scenario_refs=scenario_refs,
        adapter="anthropic",
        timer=_timer(),
        pricing=_pricing(),
        provider_usage=_usage(),
        input_texts=["prompt"],
        output_texts=["answer"],
        observed_agent_text_message_count=1,
        evidence=evidence,
        platform_observations=[
            {"kind": "message", "id": "msg-1", "scenario_refs": scenario_refs}
        ],
        l4_provider_token_split={
            "history_replay_tokens": 70,
            "new_inference_tokens": 15,
            "history_to_new_token_ratio": 70 / 15,
        },
        artifact_dir=tmp_path,
    )

    artifact = json.loads(path.read_text())
    assert artifact["scenario_id"] == "L4.request.cold_start_history"
    assert artifact["scenario_refs"] == sorted(scenario_refs)
    assert set(artifact["evidence"]) == set(scenario_refs)
    assert artifact["l4_provider_token_split"]["history_replay_tokens"] == 70


def test_write_baseline_tier2_artifact_defaults_to_non_claude_artifact_dir(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("E2E_BASELINE_ARTIFACT_DIR", raising=False)

    path = write_baseline_tier2_artifact(
        scenario_id="L4.request.cold_start_history",
        scenario_refs=["L4.request.cold_start_history"],
        adapter="anthropic",
        timer=_timer(),
        pricing=_pricing(),
        provider_usage=_usage(),
        input_texts=["prompt"],
        output_texts=["answer"],
        observed_agent_text_message_count=1,
        evidence=_row_evidence(),
        platform_observations=[_row_observation()],
    )

    assert path.parent.resolve() == tmp_path / "artifacts" / "e2e-baseline-artifacts"


def test_write_baseline_tier2_artifact_requires_platform_observations(tmp_path) -> None:
    with pytest.raises(
        AssertionError,
        match="successful artifact requires platform_observations",
    ):
        write_baseline_tier2_artifact(
            scenario_id="L4.request.cold_start_history",
            scenario_refs=["L4.request.cold_start_history"],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence=_row_evidence(),
            platform_observations=[],
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_requires_row_specific_evidence(
    tmp_path,
) -> None:
    with pytest.raises(
        AssertionError,
        match="missing row-specific artifact evidence for L2.request.earliest_turn",
    ):
        write_baseline_tier2_artifact(
            scenario_id="L2.request.full_history",
            scenario_refs=["L2.request.full_history", "L2.request.earliest_turn"],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence={"L2.request.full_history": {"reply_count": 1}},
            platform_observations=[_row_observation("L2.request.full_history")],
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_requires_l1_custom_tool_name(tmp_path) -> None:
    evidence = _l1_evidence()
    evidence["L1.dispatch.custom_tool"] = {
        "custom_tool_name": "logkeyword",
        "custom_tool_args": {"message": "M1_PROBE"},
        "custom_tool_calls": 1,
        "custom_tool_return_seen": True,
    }

    with pytest.raises(AssertionError, match="custom_tool_name"):
        write_baseline_tier2_artifact(
            scenario_id="L1.request.custom_prompt_present",
            scenario_refs=[
                "L1.request.custom_prompt_present",
                "L1.request.custom_prompt_additive",
                "L1.dispatch.custom_tool",
            ],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence=evidence,
            platform_observations=[
                {"kind": "message", "id": "msg-1", "scenario_refs": list(evidence)}
            ],
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_requires_l1_live_room_answer_proof(
    tmp_path,
) -> None:
    evidence = _l1_evidence()
    evidence["L1.request.custom_prompt_additive"] = {
        "platform_live_user_seen": False,
        "platform_non_participant_absent": True,
        "platform_observation_source": "live_room_answer",
    }

    with pytest.raises(AssertionError, match="platform_live_user_seen"):
        write_baseline_tier2_artifact(
            scenario_id="L1.request.custom_prompt_present",
            scenario_refs=[
                "L1.request.custom_prompt_present",
                "L1.request.custom_prompt_additive",
                "L1.dispatch.custom_tool",
            ],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence=evidence,
            platform_observations=[
                {"kind": "message", "id": "msg-1", "scenario_refs": list(evidence)}
            ],
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_records_valid_l1_evidence(tmp_path) -> None:
    evidence = _l1_evidence()
    path = write_baseline_tier2_artifact(
        scenario_id="L1.request.custom_prompt_present",
        scenario_refs=[
            "L1.request.custom_prompt_present",
            "L1.request.custom_prompt_additive",
            "L1.dispatch.custom_tool",
        ],
        adapter="anthropic",
        timer=_timer(),
        pricing=_pricing(),
        provider_usage=_usage(),
        input_texts=["prompt TOKEN=sk-proj-secretsecretsecret"],
        output_texts=["answer from thnv_a_secretsecretsecret"],
        observed_agent_text_message_count=2,
        evidence=evidence,
        platform_observations=[
            {"kind": "message", "id": "msg-1", "scenario_refs": list(evidence)}
        ],
        artifact_dir=tmp_path,
    )

    artifact = json.loads(path.read_text())
    assert artifact["evidence"] == evidence
    assert artifact["input_texts"] == ["prompt TOKEN=<redacted>"]
    assert artifact["output_texts"] == ["answer from <redacted>"]


def test_write_baseline_tier2_artifact_requires_provider_usage(tmp_path) -> None:
    with pytest.raises(AssertionError, match="provider input/output token counts"):
        write_baseline_tier2_artifact(
            scenario_id="L4.request.cold_start_history",
            scenario_refs=["L4.request.cold_start_history"],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=BaselineProviderUsage(
                api_call_count=1,
                input_tokens=0,
                output_tokens=1,
                total_tokens=1,
                source="provider.test.usage",
                raw_snapshots=[],
            ),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence={},
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_requires_l4_provider_split_shape(
    tmp_path,
) -> None:
    with pytest.raises(AssertionError, match="incomplete L4 provider token split"):
        write_baseline_tier2_artifact(
            scenario_id="L4.request.cold_start_history",
            scenario_refs=["L4.request.cold_start_history"],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence=_row_evidence(),
            platform_observations=[_row_observation()],
            l4_provider_token_split={"history_replay_tokens": 1},
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_artifact_requires_positive_l4_provider_split(
    tmp_path,
) -> None:
    with pytest.raises(
        AssertionError,
        match="L4 provider token split values must be positive",
    ):
        write_baseline_tier2_artifact(
            scenario_id="L4.request.cold_start_history",
            scenario_refs=["L4.request.cold_start_history"],
            adapter="anthropic",
            timer=_timer(),
            pricing=_pricing(),
            provider_usage=_usage(),
            input_texts=["prompt"],
            output_texts=["answer"],
            observed_agent_text_message_count=1,
            evidence=_row_evidence(),
            platform_observations=[_row_observation()],
            l4_provider_token_split={
                "history_replay_tokens": 0,
                "new_inference_tokens": 1,
                "history_to_new_token_ratio": 1,
            },
            artifact_dir=tmp_path,
        )


def test_write_baseline_tier2_blocked_artifact_records_row_level_reasons(
    tmp_path,
) -> None:
    path = write_baseline_tier2_blocked_artifact(
        scenario_id="L2.request.full_history",
        scenario_refs=[
            "L2.request.full_history",
            "L2.request.earliest_turn",
        ],
        adapter="codex",
        reason="tier2_blocked: no provider usage seam",
        artifact_dir=tmp_path,
    )

    artifact = json.loads(path.read_text())
    assert artifact["status"] == "TIER2_BLOCKED"
    assert artifact["adapter"] == "codex"
    assert artifact["llm_api_call_count"] is None
    assert artifact["input_tokens"] is None
    assert artifact["output_tokens"] is None
    assert artifact["estimated_usd"] is None
    assert artifact["blocked_scenarios"] == {
        "L2.request.earliest_turn": "tier2_blocked: no provider usage seam",
        "L2.request.full_history": "tier2_blocked: no provider usage seam",
    }
