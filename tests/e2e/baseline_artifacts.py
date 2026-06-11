"""Machine-readable Tier-2 baseline evidence artifacts.

These helpers intentionally live in the E2E test tree: they record what a live
baseline run observed without adding production SDK behavior or adapter-specific
branches.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from thenvoi.core.simple_adapter import ProviderUsageSnapshot, SimpleAdapter
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID


_DEFAULT_ARTIFACT_DIR = Path(".claude/reports/e2e-baseline-artifacts")


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
        "token_count_source": "provider_reported_usage",
        "provider_usage": {
            "source": provider_usage.source,
            "raw_snapshots": provider_usage.raw_snapshots,
        },
        "estimated_usd": round(estimated_usd, 8),
        "pricing": {
            "input_usd_per_million_tokens": pricing.input_usd_per_million_tokens,
            "output_usd_per_million_tokens": pricing.output_usd_per_million_tokens,
            "source": pricing.source,
        },
        "platform_observations": platform_observations or [],
        "evidence": evidence,
    }
    if l4_provider_token_split is not None:
        missing = {
            "pre_restart_input_tokens",
            "pre_restart_output_tokens",
            "post_restart_input_tokens",
            "post_restart_output_tokens",
        } - set(l4_provider_token_split)
        if missing:
            raise AssertionError(
                "tier2_blocked: incomplete L4 provider token split "
                + ", ".join(sorted(missing))
            )
        non_positive = sorted(
            key
            for key in (
                "pre_restart_input_tokens",
                "pre_restart_output_tokens",
                "post_restart_input_tokens",
                "post_restart_output_tokens",
            )
            if l4_provider_token_split[key] <= 0
        )
        if non_positive:
            raise AssertionError(
                "tier2_blocked: L4 provider token split values must be positive "
                + ", ".join(non_positive)
            )
        artifact["l4_provider_token_split"] = {
            **l4_provider_token_split,
            "source": "provider_reported_usage_by_adapter_instance",
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
