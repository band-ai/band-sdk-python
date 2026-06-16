from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# The repo root must be on sys.path before these imports when this file is
# executed directly as ``uv run python tests/baseline_adapter_scorecard.py``.
from tests.framework_conformance.baseline_applicability import (  # noqa: E402
    ApplicabilityCell,
    ApplicabilityStatus,
    BASELINE_ADAPTER_IDS,
    build_applicability_matrix,
)
from tests.framework_conformance.baseline_scenarios import SCENARIOS_BY_ID  # noqa: E402
from tests.framework_conformance.injection_registry import (  # noqa: E402
    TIER2_L0_BLOCKED_COVERAGE,
    bindings_by_adapter,
    tier1_dependency_blocked_reason,
)

_DEFAULT_ARTIFACT_DIR = _REPO_ROOT / "artifacts" / "e2e-baseline-artifacts"
_BASELINE_ARTIFACTS_SOURCE = _REPO_ROOT / "tests" / "e2e" / "baseline_artifacts.py"

_TIER1_SCENARIO_TARGETS = {
    "L0": "tests/framework_conformance/test_baseline_l0_platform.py",
    "L1": "tests/framework_conformance/test_baseline_l1_customization.py",
    "L2": "tests/framework_conformance/test_baseline_l2_context.py",
    "L3": "tests/framework_conformance/test_baseline_l3_multiparty.py",
    "L4": "tests/framework_conformance/test_baseline_l4_rehydration.py",
}
_TIER1_META_TARGETS = (
    "tests/framework_conformance/test_baseline_scorecard.py",
    "tests/framework_conformance/test_injection_binding_drift.py",
    "tests/framework_conformance/test_injection_canary.py",
)
_TIER2_SCENARIO_TARGETS = {
    "L0": "tests/e2e/scenarios/test_baseline_l0_platform.py",
    "L1": "tests/e2e/scenarios/test_baseline_l1_customization.py",
    "L2": "tests/e2e/scenarios/test_baseline_l2_context.py",
    "L3": "tests/e2e/scenarios/test_baseline_l3_multiparty.py",
    "L4": "tests/e2e/scenarios/test_baseline_l4_rehydration.py",
}

TierSelection = Literal["tier1", "tier2", "all"]
OutputFormat = Literal["text", "json"]


@dataclass(frozen=True, kw_only=True)
class CommandResult:
    name: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, kw_only=True)
class ScenarioScore:
    scenario_id: str
    level: str
    title: str
    applicability: str
    status: str
    reason: str | None = None
    capture_family: str | None = None
    base_instruction_surface: str | None = None
    tier2_pointer: str | None = None
    artifact_paths: list[str] | None = None


@dataclass(frozen=True, kw_only=True)
class AdapterScorecard:
    adapter: str
    generated_at: str
    tier: TierSelection
    levels: list[str]
    artifact_dir: str
    tier1_dependency_blocked_reason: str | None
    provider_usage_blocked_reason: str | None
    commands: list[CommandResult]
    scenarios: list[ScenarioScore]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the baseline conformance scorecard harness for one adapter. "
            "Tier 1 is deterministic and local. Tier 2 is live-gated by the "
            "existing E2E_BASELINE_* environment variables."
        )
    )
    parser.add_argument(
        "--adapter",
        required=True,
        choices=sorted(BASELINE_ADAPTER_IDS),
        help="Adapter id, e.g. langgraph, anthropic, pydantic_ai, codex.",
    )
    parser.add_argument(
        "--tier",
        choices=("tier1", "tier2", "all"),
        default="all",
        help="Which proof tier to run.",
    )
    parser.add_argument(
        "--levels",
        default="L0,L1,L2,L3,L4",
        help="Comma-separated baseline levels to run, e.g. L0,L2,L4.",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=_DEFAULT_ARTIFACT_DIR,
        help="Directory for live Tier 2 artifacts and scorecard output.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON scorecard output path. Defaults under artifact-dir.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Console output format.",
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Extra argument to append to every pytest invocation. Repeat as needed.",
    )
    parser.add_argument(
        "--no-run",
        action="store_true",
        help="Only render the static scorecard and existing artifacts; do not run pytest.",
    )
    return parser.parse_args()


def _selected_levels(raw_levels: str) -> list[str]:
    levels = [level.strip().upper() for level in raw_levels.split(",") if level.strip()]
    unknown = sorted(set(levels) - set(_TIER1_SCENARIO_TARGETS))
    if unknown:
        raise SystemExit(f"Unknown baseline level(s): {', '.join(unknown)}")
    return levels


def _provider_usage_supported_adapters() -> frozenset[str]:
    """Read provider-usage support without importing live SDK modules."""

    tree = ast.parse(
        _BASELINE_ARTIFACTS_SOURCE.read_text(encoding="utf-8"),
        filename=str(_BASELINE_ARTIFACTS_SOURCE),
    )
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name)
            and target.id == "_PROVIDER_USAGE_SUPPORTED_ADAPTERS"
            for target in node.targets
        ):
            continue
        value = node.value
        if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
            break
        if value.func.id != "frozenset" or len(value.args) != 1:
            break
        literal = ast.literal_eval(value.args[0])
        if not isinstance(literal, set):
            break
        return frozenset(str(item) for item in literal)
    raise AssertionError("Could not read _PROVIDER_USAGE_SUPPORTED_ADAPTERS")


def _provider_usage_blocked_reason(adapter: str) -> str | None:
    if adapter in _provider_usage_supported_adapters():
        return None
    return (
        "tier2_blocked: adapter does not expose provider-owned input/output token "
        f"usage for baseline cost proof: {adapter}"
    )


def _run_command(name: str, command: list[str], env: dict[str, str]) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(
        name=name,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _pytest_command(paths: list[str], adapter: str, extra_args: list[str]) -> list[str]:
    return [
        sys.executable,
        "-m",
        "pytest",
        *paths,
        "-k",
        adapter,
        "--tb=short",
        *extra_args,
    ]


def _run_tier1(
    adapter: str, levels: list[str], extra_args: list[str]
) -> list[CommandResult]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    results: list[CommandResult] = []
    scenario_paths = [_TIER1_SCENARIO_TARGETS[level] for level in levels]
    if scenario_paths:
        results.append(
            _run_command(
                "tier1_adapter_scenarios",
                _pytest_command(scenario_paths, adapter, extra_args),
                env,
            )
        )
    results.append(
        _run_command(
            "tier1_registry_meta",
            [
                sys.executable,
                "-m",
                "pytest",
                *_TIER1_META_TARGETS,
                "--tb=short",
                *extra_args,
            ],
            env,
        )
    )
    return results


def _run_tier2(
    adapter: str,
    levels: list[str],
    artifact_dir: Path,
    extra_args: list[str],
) -> list[CommandResult]:
    env = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "E2E_BASELINE_ARTIFACT_DIR": str(artifact_dir),
    }
    scenario_paths = [_TIER2_SCENARIO_TARGETS[level] for level in levels]
    if not scenario_paths:
        return []
    return [
        _run_command(
            "tier2_live_scenarios",
            _pytest_command(scenario_paths, adapter, extra_args),
            env,
        )
    ]


def _artifact_records(artifact_dir: Path, adapter: str) -> dict[str, list[Path]]:
    records: dict[str, list[Path]] = {}
    if not artifact_dir.exists():
        return records
    for path in sorted(artifact_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("adapter") != adapter:
            continue
        refs = data.get("scenario_refs") or [data.get("scenario_id")]
        if isinstance(refs, str):
            refs = [refs]
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if isinstance(ref, str):
                records.setdefault(ref, []).append(path)
    return records


def _scenario_status(
    cell: ApplicabilityCell,
    artifact_paths: list[Path],
) -> tuple[str, str | None]:
    if artifact_paths:
        blocked = any(path.name.endswith("-blocked.json") for path in artifact_paths)
        if blocked:
            return "tier2_blocked_artifact", "live artifact recorded blocked status"
        return "tier2_artifact_observed", None
    if cell.status is ApplicabilityStatus.APPLICABLE:
        return "tier1_applicable", None
    return cell.status.value, cell.reason


def _build_scorecard(
    *,
    adapter: str,
    tier: TierSelection,
    levels: list[str],
    artifact_dir: Path,
    commands: list[CommandResult],
) -> AdapterScorecard:
    artifact_records = _artifact_records(artifact_dir, adapter)
    selected_level_set = set(levels)
    cells = build_applicability_matrix(frozenset({adapter}))
    scenario_scores: list[ScenarioScore] = []
    for cell in cells:
        scenario = SCENARIOS_BY_ID[cell.scenario_id]
        level = scenario.id.split(".", 1)[0]
        if level not in selected_level_set:
            continue
        paths = artifact_records.get(cell.scenario_id, [])
        status, reason = _scenario_status(cell, paths)
        scenario_scores.append(
            ScenarioScore(
                scenario_id=cell.scenario_id,
                level=level,
                title=scenario.title,
                applicability=cell.status.value,
                status=status,
                reason=reason,
                capture_family=cell.capture_family,
                base_instruction_surface=cell.base_instruction_surface,
                tier2_pointer=cell.tier2_pointer,
                artifact_paths=[str(path) for path in paths] or None,
            )
        )
    binding = bindings_by_adapter().get(adapter)
    tier1_blocked_reason = (
        tier1_dependency_blocked_reason(binding) if binding is not None else None
    )
    provider_blocked_reason = _provider_usage_blocked_reason(adapter)
    explicit_l0_block = TIER2_L0_BLOCKED_COVERAGE.get(adapter)
    if explicit_l0_block is not None and provider_blocked_reason is None:
        provider_blocked_reason = explicit_l0_block.reason
    return AdapterScorecard(
        adapter=adapter,
        generated_at=datetime.now(UTC).isoformat(),
        tier=tier,
        levels=levels,
        artifact_dir=str(artifact_dir),
        tier1_dependency_blocked_reason=tier1_blocked_reason,
        provider_usage_blocked_reason=provider_blocked_reason,
        commands=commands,
        scenarios=scenario_scores,
    )


def _write_scorecard(scorecard: AdapterScorecard, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(asdict(scorecard), allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _render_text(scorecard: AdapterScorecard, output_path: Path) -> str:
    failed_commands = [
        command for command in scorecard.commands if command.exit_code != 0
    ]
    lines = [
        f"Adapter baseline scorecard: {scorecard.adapter}",
        f"Tier: {scorecard.tier}",
        f"Levels: {', '.join(scorecard.levels)}",
        f"Artifact dir: {scorecard.artifact_dir}",
        f"Scorecard JSON: {output_path}",
    ]
    if scorecard.tier1_dependency_blocked_reason:
        lines.append(
            f"Tier 1 dependency block: {scorecard.tier1_dependency_blocked_reason}"
        )
    if scorecard.provider_usage_blocked_reason:
        lines.append(
            f"Tier 2 provider/live block: {scorecard.provider_usage_blocked_reason}"
        )
    lines.append("")
    lines.append("Commands:")
    if not scorecard.commands:
        lines.append("  not run (--no-run)")
    for command in scorecard.commands:
        outcome = "PASS" if command.exit_code == 0 else f"FAIL({command.exit_code})"
        lines.append(f"  {outcome} {command.name}: {' '.join(command.command)}")
    lines.append("")
    lines.append("Rows:")
    for scenario in scorecard.scenarios:
        lines.append(
            "  "
            f"{scenario.status:24} {scenario.scenario_id} "
            f"[{scenario.applicability}] {scenario.title}"
        )
        if scenario.reason:
            lines.append(f"    reason: {scenario.reason}")
        if scenario.artifact_paths:
            for path in scenario.artifact_paths:
                lines.append(f"    artifact: {path}")
    lines.append("")
    if failed_commands:
        lines.append(
            "Verdict: failing command(s) need inspection before claiming green."
        )
    else:
        lines.append(
            "Verdict: command layer passed or was not run; inspect row statuses and artifacts."
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parse_args()
    adapter = str(args.adapter)
    tier = str(args.tier)
    levels = _selected_levels(str(args.levels))
    artifact_dir = Path(args.artifact_dir).resolve()
    output = (
        Path(args.output).resolve()
        if args.output
        else artifact_dir / f"scorecard-{adapter}.json"
    )

    commands: list[CommandResult] = []
    if not args.no_run:
        if tier in {"tier1", "all"}:
            commands.extend(_run_tier1(adapter, levels, list(args.pytest_arg)))
        if tier in {"tier2", "all"}:
            commands.extend(
                _run_tier2(adapter, levels, artifact_dir, list(args.pytest_arg))
            )

    scorecard = _build_scorecard(
        adapter=adapter,
        tier=tier,
        levels=levels,
        artifact_dir=artifact_dir,
        commands=commands,
    )
    _write_scorecard(scorecard, output)
    if args.format == "json":
        sys.stdout.write(
            json.dumps(asdict(scorecard), allow_nan=False, indent=2, sort_keys=True)
            + "\n"
        )
    else:
        sys.stdout.write(_render_text(scorecard, output))
    return 1 if any(command.exit_code != 0 for command in commands) else 0


if __name__ == "__main__":
    raise SystemExit(main())
