"""Machine-readable Tier-2 baseline evidence artifacts.

These helpers intentionally live in the E2E test tree: they record what a live
baseline run observed without adding production SDK behavior or adapter-specific
branches.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from band.core.simple_adapter import ProviderUsageSnapshot, SimpleAdapter
from tests.baseline_l1_fixtures import L1_CUSTOM_TOOL_NAME
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID


_DEFAULT_ARTIFACT_DIR = Path("artifacts/e2e-baseline-artifacts")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*\s*[:=]\s*)"
    r"(\"[^\"]+\"|'[^']+'|[^\s,}]+)"
)
_SECRET_VALUE_PATTERN = re.compile(
    r"\b(?:sk-proj-[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|"
    r"thnv_[au]_[A-Za-z0-9_-]{8,}|band_[au]_[A-Za-z0-9_-]{8,}|"
    r"AIza[0-9A-Za-z_-]{20,})\b"
)


@dataclass(frozen=True, kw_only=True)
class BaselinePricing:
    input_usd_per_million_tokens: float
    output_usd_per_million_tokens: float
    source: str


@dataclass(frozen=True, kw_only=True)
class BaselineTier2Timer:
    started_at: str
    start_monotonic: float


@dataclass(frozen=True, kw_only=True)
class BaselineProviderUsage:
    api_call_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    source: str
    raw_snapshots: list[dict[str, Any]]
    cost_usd: float | None = None


_PROVIDER_USAGE_SUPPORTED_ADAPTERS = frozenset(
    {"anthropic", "claude_sdk", "gemini", "langgraph", "pydantic_ai"}
)


def start_baseline_tier2_timer() -> BaselineTier2Timer:
    return BaselineTier2Timer(
        started_at=datetime.now(UTC).isoformat(),
        start_monotonic=time.perf_counter(),
    )


def baseline_pricing_from_env() -> BaselinePricing:
    """Load explicit live-run pricing inputs.

    The baseline spec requires estimated dollar cost. Keeping prices in env vars
    prevents stale model pricing from being baked into tests while making live
    runs fail closed if the runner did not choose a pricing source.
    """

    input_rate = os.environ.get("E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS")
    output_rate = os.environ.get("E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS")
    source = os.environ.get("E2E_BASELINE_PRICING_SOURCE")
    missing = [
        name
        for name, value in (
            ("E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS", input_rate),
            ("E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS", output_rate),
            ("E2E_BASELINE_PRICING_SOURCE", source),
        )
        if not value
    ]
    if missing:
        raise AssertionError(
            "tier2_blocked: missing cost metadata configuration " + ", ".join(missing)
        )

    try:
        rates = {
            "E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS": float(input_rate),
            "E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS": float(output_rate),
        }
    except ValueError as exc:
        raise AssertionError(
            "tier2_blocked: invalid cost metadata configuration"
        ) from exc
    invalid = [
        name for name, value in rates.items() if not math.isfinite(value) or value <= 0
    ]
    if invalid:
        raise AssertionError(
            "tier2_blocked: invalid cost metadata configuration " + ", ".join(invalid)
        )

    return BaselinePricing(
        input_usd_per_million_tokens=rates["E2E_BASELINE_INPUT_USD_PER_MILLION_TOKENS"],
        output_usd_per_million_tokens=rates[
            "E2E_BASELINE_OUTPUT_USD_PER_MILLION_TOKENS"
        ],
        source=str(source),
    )


def estimate_tokens(texts: list[str]) -> int:
    """Conservative deterministic token estimate for sanitized non-proof metadata."""

    chars = sum(len(text) for text in texts)
    return max(1, (chars + 3) // 4) if texts else 0


def provider_usage_blocked_reason(adapter: str) -> str | None:
    if adapter in _PROVIDER_USAGE_SUPPORTED_ADAPTERS:
        return None
    if adapter == "parlant":
        return (
            "tier2_blocked: Parlant baseline proof requires a dedicated in-process "
            "Parlant server runner and does not expose provider-owned input/output "
            "token usage for baseline cost proof"
        )
    return (
        "tier2_blocked: adapter does not expose provider-owned input/output token "
        f"usage for baseline cost proof: {adapter}"
    )


def write_provider_usage_blocked_artifact_if_needed(
    *,
    scenario_id: str,
    scenario_refs: list[str],
    adapter: str,
    artifact_dir: Path | None = None,
) -> str | None:
    reason = provider_usage_blocked_reason(adapter)
    if reason is None:
        return None
    write_baseline_tier2_blocked_artifact(
        scenario_id=scenario_id,
        scenario_refs=scenario_refs,
        adapter=adapter,
        reason=reason,
        artifact_dir=artifact_dir,
    )
    return reason


def provider_usage_from_adapter(
    adapter: SimpleAdapter[Any],
    *,
    adapter_name: str,
) -> BaselineProviderUsage:
    snapshots = adapter.provider_usage_snapshots()
    if not snapshots:
        raise AssertionError(
            "tier2_blocked: adapter did not record provider-owned usage: "
            f"{adapter_name}"
        )
    return aggregate_provider_usage(snapshots)


def l0_usage_from_live_observation(
    *,
    adapter: SimpleAdapter[Any],
    adapter_name: str,
    input_texts: list[str],
    output_texts: list[str],
    observed_agent_text_message_count: int,
) -> BaselineProviderUsage:
    """Return provider usage when available, otherwise L0-only text estimates.

    L0 proves live Band platform adaptation. Its approved pass/fail contract does
    not depend on provider token/cost reporting, so missing provider-owned usage
    must not block an otherwise valid L0 full-flow artifact.
    """

    snapshots = adapter.provider_usage_snapshots()
    if snapshots:
        return aggregate_provider_usage(snapshots)
    input_tokens = estimate_tokens(input_texts)
    output_tokens = estimate_tokens(output_texts)
    return BaselineProviderUsage(
        api_call_count=max(1, observed_agent_text_message_count),
        input_tokens=max(1, input_tokens),
        output_tokens=max(1, output_tokens),
        total_tokens=max(1, input_tokens + output_tokens),
        source=f"l0_deterministic_text_estimate_provider_usage_unavailable:{adapter_name}",
        raw_snapshots=[],
    )


def aggregate_provider_usage(
    snapshots: list[ProviderUsageSnapshot],
) -> BaselineProviderUsage:
    api_call_count = sum(snapshot.api_call_count for snapshot in snapshots)
    input_tokens = sum(snapshot.input_tokens for snapshot in snapshots)
    output_tokens = sum(snapshot.output_tokens for snapshot in snapshots)
    total_tokens = sum(snapshot.total_tokens for snapshot in snapshots)
    cost_usd = None
    if snapshots and all(snapshot.cost_usd is not None for snapshot in snapshots):
        cost_usd = sum(snapshot.cost_usd or 0 for snapshot in snapshots)
    return BaselineProviderUsage(
        api_call_count=api_call_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        source=" + ".join(dict.fromkeys(snapshot.source for snapshot in snapshots)),
        raw_snapshots=[
            {
                "source": snapshot.source,
                "input_tokens": snapshot.input_tokens,
                "output_tokens": snapshot.output_tokens,
                "total_tokens": snapshot.total_tokens,
                "api_call_count": snapshot.api_call_count,
                "cost_usd": snapshot.cost_usd,
                "raw": snapshot.raw,
            }
            for snapshot in snapshots
        ],
        cost_usd=cost_usd,
    )


def _validate_scenario_ids(scenario_id: str, scenario_refs: list[str]) -> None:
    scenario_ids = [scenario_id, *scenario_refs]
    unknown_scenarios = sorted(
        {scenario for scenario in scenario_ids if scenario not in SCENARIOS_BY_ID}
    )
    if unknown_scenarios:
        raise AssertionError(
            "tier2_blocked: unknown baseline scenario id "
            + ", ".join(unknown_scenarios)
        )
    if not scenario_refs:
        raise AssertionError("tier2_blocked: scenario_refs must name proven rows")


def _sanitize_artifact_text(text: str) -> str:
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}<redacted>", text
    )
    return _SECRET_VALUE_PATTERN.sub("<redacted>", sanitized)


def _sanitize_artifact_texts(texts: list[str]) -> list[str]:
    return [_sanitize_artifact_text(str(text)) for text in texts]


def _sanitize_artifact_value(value: Any) -> Any:
    if isinstance(value, str):
        return _sanitize_artifact_text(value)
    if isinstance(value, list):
        return [_sanitize_artifact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_artifact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_artifact_value(item) for key, item in value.items()}
    return value


def _observation_scenario_refs(observation: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    scenario_ref = observation.get("scenario_ref")
    if scenario_ref:
        refs.add(str(scenario_ref))

    scenario_refs = observation.get("scenario_refs")
    if isinstance(scenario_refs, str):
        refs.add(scenario_refs)
    elif isinstance(scenario_refs, (list, tuple, set)):
        refs.update(str(ref) for ref in scenario_refs)
    return refs


def _require_evidence_mapping(
    evidence: dict[str, Any],
    scenario_ref: str,
) -> dict[str, Any]:
    row = evidence.get(scenario_ref)
    if not isinstance(row, dict):
        raise AssertionError(
            f"tier2_blocked: missing structured L1 evidence for {scenario_ref}"
        )
    return row


def _require_truthy(row: dict[str, Any], scenario_ref: str, key: str) -> None:
    if not row.get(key):
        raise AssertionError(
            f"tier2_blocked: missing L1 proof field {scenario_ref}.{key}"
        )


def _validate_l1_successful_artifact_evidence(
    *,
    scenario_refs: list[str],
    evidence: dict[str, Any],
) -> None:
    claimed_refs = set(scenario_refs)
    if "L1.request.custom_prompt_present" in claimed_refs:
        row = _require_evidence_mapping(evidence, "L1.request.custom_prompt_present")
        _require_truthy(row, "L1.request.custom_prompt_present", "custom_prompt_marker")
        marker_steps = row.get("custom_prompt_marker_seen_in_steps")
        if not isinstance(marker_steps, list) or not marker_steps:
            raise AssertionError(
                "tier2_blocked: missing L1 proof field "
                "L1.request.custom_prompt_present.custom_prompt_marker_seen_in_steps"
            )

    if "L1.request.custom_prompt_additive" in claimed_refs:
        row = _require_evidence_mapping(evidence, "L1.request.custom_prompt_additive")
        expected_values = {
            "platform_live_user_seen": True,
            "platform_non_participant_absent": True,
            "platform_observation_source": "live_room_answer",
        }
        for key, expected in expected_values.items():
            if row.get(key) != expected:
                raise AssertionError(
                    f"tier2_blocked: invalid L1 proof field "
                    f"L1.request.custom_prompt_additive.{key}"
                )

    if "L1.dispatch.custom_tool" in claimed_refs:
        row = _require_evidence_mapping(evidence, "L1.dispatch.custom_tool")
        if row.get("custom_tool_name") != L1_CUSTOM_TOOL_NAME:
            raise AssertionError(
                "tier2_blocked: invalid L1 proof field "
                "L1.dispatch.custom_tool.custom_tool_name"
            )
        calls = row.get("custom_tool_calls")
        if not isinstance(calls, int) or calls <= 0:
            raise AssertionError(
                "tier2_blocked: invalid L1 proof field "
                "L1.dispatch.custom_tool.custom_tool_calls"
            )
        custom_tool_args = row.get("custom_tool_args")
        if not isinstance(custom_tool_args, dict) or not custom_tool_args.get(
            "message"
        ):
            raise AssertionError(
                "tier2_blocked: missing L1 proof field "
                "L1.dispatch.custom_tool.custom_tool_args"
            )
        if row.get("custom_tool_return_seen") is not True:
            raise AssertionError(
                "tier2_blocked: invalid L1 proof field "
                "L1.dispatch.custom_tool.custom_tool_return_seen"
            )


def _validate_successful_artifact_evidence(
    *,
    scenario_refs: list[str],
    evidence: dict[str, Any],
    platform_observations: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if not platform_observations:
        raise AssertionError(
            "tier2_blocked: successful artifact requires platform_observations"
        )

    claimed_refs = set(scenario_refs)
    evidenced_refs = {ref for ref in claimed_refs if ref in evidence}
    for observation in platform_observations:
        evidenced_refs.update(_observation_scenario_refs(observation) & claimed_refs)

    missing_refs = sorted(claimed_refs - evidenced_refs)
    if missing_refs:
        raise AssertionError(
            "tier2_blocked: missing row-specific artifact evidence for "
            + ", ".join(missing_refs)
        )
    _validate_l1_successful_artifact_evidence(
        scenario_refs=scenario_refs,
        evidence=evidence,
    )
    return platform_observations


def write_baseline_tier2_artifact(
    *,
    scenario_id: str,
    scenario_refs: list[str],
    adapter: str,
    timer: BaselineTier2Timer,
    pricing: BaselinePricing,
    provider_usage: BaselineProviderUsage,
    input_texts: list[str],
    output_texts: list[str],
    observed_agent_text_message_count: int,
    evidence: dict[str, Any],
    platform_observations: list[dict[str, Any]] | None = None,
    l4_provider_token_split: dict[str, int] | None = None,
    l4_scenario_text_token_estimate: dict[str, int] | None = None,
    artifact_dir: Path | None = None,
) -> Path:
    """Write the artifact contract used by the readiness ledger."""

    _validate_scenario_ids(scenario_id, scenario_refs)
    if observed_agent_text_message_count <= 0:
        raise AssertionError(
            "tier2_blocked: observed_agent_text_message_count must be positive"
        )

    if provider_usage.api_call_count <= 0:
        raise AssertionError("tier2_blocked: provider api_call_count must be positive")
    if provider_usage.input_tokens <= 0 or provider_usage.output_tokens <= 0:
        raise AssertionError(
            "tier2_blocked: provider input/output token counts must be positive"
        )
    platform_observations = _validate_successful_artifact_evidence(
        scenario_refs=scenario_refs,
        evidence=evidence,
        platform_observations=platform_observations,
    )
    sanitized_input_texts = _sanitize_artifact_texts(input_texts)
    sanitized_output_texts = _sanitize_artifact_texts(output_texts)
    sanitized_platform_observations = _sanitize_artifact_value(platform_observations)
    sanitized_evidence = _sanitize_artifact_value(evidence)
    sanitized_raw_snapshots = _sanitize_artifact_value(provider_usage.raw_snapshots)
    estimated_usd = provider_usage.cost_usd
    if estimated_usd is None:
        estimated_usd = (
            provider_usage.input_tokens * pricing.input_usd_per_million_tokens
            + provider_usage.output_tokens * pricing.output_usd_per_million_tokens
        ) / 1_000_000
    ended_at = datetime.now(UTC).isoformat()
    wall_clock_ms = int((time.perf_counter() - timer.start_monotonic) * 1000)
    run_id = os.environ.get(
        "E2E_BASELINE_RUN_ID", datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    safe_scenario = scenario_id.replace("/", "-").replace(".", "-")
    safe_adapter = adapter.replace("/", "-").replace(".", "-")
    target_dir = artifact_dir or Path(
        os.environ.get("E2E_BASELINE_ARTIFACT_DIR", str(_DEFAULT_ARTIFACT_DIR))
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{run_id}-{safe_scenario}-{safe_adapter}.json"

    token_count_source = (
        "l0_deterministic_text_estimate_provider_usage_unavailable"
        if provider_usage.source.startswith(
            "l0_deterministic_text_estimate_provider_usage_unavailable:"
        )
        else "provider_reported_usage"
    )
    artifact: dict[str, Any] = {
        "schema_version": 1,
        "level": scenario_id.split(".", 1)[0],
        "scenario": scenario_id,
        "scenario_id": scenario_id,
        "scenario_refs": sorted(set(scenario_refs)),
        "adapter": adapter,
        "started_at": timer.started_at,
        "ended_at": ended_at,
        "wall_clock_ms": wall_clock_ms,
        "observed_agent_text_message_count": observed_agent_text_message_count,
        "llm_api_call_count": provider_usage.api_call_count,
        "llm_api_call_count_source": provider_usage.source,
        "input_tokens": provider_usage.input_tokens,
        "output_tokens": provider_usage.output_tokens,
        "total_tokens": provider_usage.total_tokens,
        "token_count_source": token_count_source,
        "provider_usage": {
            "source": provider_usage.source,
            "raw_snapshots": sanitized_raw_snapshots,
        },
        "estimated_usd": round(estimated_usd, 8),
        "pricing": {
            "input_usd_per_million_tokens": pricing.input_usd_per_million_tokens,
            "output_usd_per_million_tokens": pricing.output_usd_per_million_tokens,
            "source": pricing.source,
        },
        "input_texts": sanitized_input_texts,
        "output_texts": sanitized_output_texts,
        "platform_observations": sanitized_platform_observations,
        "evidence": sanitized_evidence,
    }
    if l4_provider_token_split is not None:
        missing = {
            "history_replay_tokens",
            "new_inference_tokens",
            "history_to_new_token_ratio",
        } - set(l4_provider_token_split)
        if missing:
            raise AssertionError(
                "tier2_blocked: incomplete L4 provider token split "
                + ", ".join(sorted(missing))
            )
        token_keys = ("history_replay_tokens", "new_inference_tokens")
        non_positive_tokens = sorted(
            key
            for key in token_keys
            if not isinstance(l4_provider_token_split[key], int)
            or isinstance(l4_provider_token_split[key], bool)
            or l4_provider_token_split[key] <= 0
        )
        ratio = l4_provider_token_split["history_to_new_token_ratio"]
        ratio_invalid = (
            not isinstance(ratio, int | float)
            or isinstance(ratio, bool)
            or not math.isfinite(float(ratio))
            or ratio <= 0
        )
        if non_positive_tokens or ratio_invalid:
            invalid = [*non_positive_tokens]
            if ratio_invalid:
                invalid.append("history_to_new_token_ratio")
            raise AssertionError(
                "tier2_blocked: L4 provider token split values must be positive "
                + ", ".join(invalid)
            )
        source = l4_provider_token_split.get(
            "source", "provider_reported_first_post_restart_call_proxy"
        )
        artifact["l4_provider_token_split"] = {
            **l4_provider_token_split,
            "source": source,
        }

    if l4_scenario_text_token_estimate is not None:
        missing = {
            "pre_restart_scenario_text_tokens",
            "post_restart_scenario_text_tokens",
        } - set(l4_scenario_text_token_estimate)
        if missing:
            raise AssertionError(
                "tier2_blocked: incomplete L4 scenario token estimate "
                + ", ".join(sorted(missing))
            )
        artifact["l4_scenario_text_token_estimate"] = {
            **l4_scenario_text_token_estimate,
            "source": "deterministic_estimate_from_scenario_prompts_and_visible_replies",
        }

    path.write_text(
        json.dumps(artifact, allow_nan=False, indent=2, sort_keys=True) + "\n"
    )
    return path


def write_baseline_tier2_blocked_artifact(
    *,
    scenario_id: str,
    scenario_refs: list[str],
    adapter: str,
    reason: str,
    artifact_dir: Path | None = None,
) -> Path:
    """Write an explicit row-level Tier-2 blocked artifact."""

    _validate_scenario_ids(scenario_id, scenario_refs)
    run_id = os.environ.get(
        "E2E_BASELINE_RUN_ID", datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    safe_scenario = scenario_id.replace("/", "-").replace(".", "-")
    safe_adapter = adapter.replace("/", "-").replace(".", "-")
    target_dir = artifact_dir or Path(
        os.environ.get("E2E_BASELINE_ARTIFACT_DIR", str(_DEFAULT_ARTIFACT_DIR))
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{run_id}-{safe_scenario}-{safe_adapter}-blocked.json"
    artifact = {
        "schema_version": 1,
        "status": "TIER2_BLOCKED",
        "level": scenario_id.split(".", 1)[0],
        "scenario": scenario_id,
        "scenario_id": scenario_id,
        "scenario_refs": sorted(set(scenario_refs)),
        "adapter": adapter,
        "blocked_reason": reason,
        "blocked_scenarios": {
            scenario_ref: reason for scenario_ref in sorted(set(scenario_refs))
        },
        "llm_api_call_count": None,
        "input_tokens": None,
        "output_tokens": None,
        "estimated_usd": None,
        "token_count_source": "tier2_blocked_provider_usage_unavailable",
    }
    path.write_text(
        json.dumps(artifact, allow_nan=False, indent=2, sort_keys=True) + "\n"
    )
    return path
