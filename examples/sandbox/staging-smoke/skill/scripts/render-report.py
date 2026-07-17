"""
Render `.sandbox-smoke/evidence.md` from the recorded run state — the
handoff artifact the "Evidence report contract" in the design doc specifies.
Reads only non-secret fields from state.json; never touches credentials.
"""

from __future__ import annotations

import logging

import root  # noqa: F401  (bootstraps sys.path as a side effect)

import state

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# The full run's probe-verified requirements, in workflow order. Every row is
# mandatory: a missing probe renders NOT RUN and makes the overall result
# INCOMPLETE — the full run has no optional checks.
PROBE_REQUIREMENTS: list[tuple[str, str]] = [
    ("initial", "Initial WS receive + REST reply"),
    ("after-wifi-reconnect", "Reconnect after Wi-Fi cycle"),
    ("after-sleep-wake", "Recovery after host sleep/wake"),
    ("after-daemon-restart", "Recovery after sandbox daemon restart"),
]


def _requirement_row(
    label: str, requirement: str, latest: state.ProbeResult | None
) -> str:
    """One scorecard row from the *latest* attempt for ``label`` only — a
    probe that failed once and passed on retry must read as PASS, not FAIL
    (see `SmokeState.latest`); earlier attempts are history, not the verdict.
    """
    if latest is None:
        return f"| {requirement} | NOT RUN | no probe recorded |"
    status = "PASS" if latest.passed else "FAIL"
    return f"| {requirement} | {status} | {label}: marker {latest.marker} |"


def render(run_state: state.SmokeState) -> str:
    latest_by_label = {
        label: run_state.latest(label) for label, _ in PROBE_REQUIREMENTS
    }
    any_failed = any(
        latest is not None and not latest.passed for latest in latest_by_label.values()
    )
    any_missing = any(latest is None for latest in latest_by_label.values())

    # The verdict is derived from the probes alone (plus an explicit operator
    # `failed` marking) — never from progress phases, which announce workflow
    # position and cannot fabricate or veto a result.
    if run_state.phase == "failed" or any_failed:
        overall = "FAIL"
    elif any_missing:
        overall = "INCOMPLETE"
    else:
        overall = "PASS"

    duration = run_state.updated_at - run_state.started_at

    lines = [
        f"# Sandbox staging smoke — {run_state.run_id}",
        "",
        "## Result",
        "",
        f"{overall} — phase: {run_state.phase}",
        "",
        "## Environment",
        "",
        f"- Duration: {duration}",
        f"- SDK version: {run_state.sdk_version or 'unknown'}",
        f"- sbx version: {run_state.sbx_version or 'unknown'}",
        f"- Sandbox name: {run_state.sandbox_name or 'unknown'}",
        "",
        "## Requirement scorecard",
        "",
        "| Requirement | Status | Evidence |",
        "|---|---|---|",
    ]
    lines += [
        _requirement_row(label, requirement, latest_by_label[label])
        for label, requirement in PROBE_REQUIREMENTS
    ]

    # Behavioral observations that accompany the probes (e.g. whether the
    # process/VM survived an interruption and what recovery took). These
    # describe *how* a recovery happened; the probe rows above are the
    # pass/fail verdicts. Every expected check renders — a missing note is
    # visibly "(not recorded)" rather than silently absent from a PASS report.
    lines += ["", "## Observed behavior", ""]
    for check_name in state.OBSERVATION_CHECKS:
        observation = run_state.residual_checks.get(check_name, "(not recorded)")
        lines.append(f"- **{check_name}**: {observation}")
    for check_name, observation in run_state.residual_checks.items():
        if check_name not in state.OBSERVATION_CHECKS:
            lines.append(f"- **{check_name}**: {observation}")

    lines += ["", "## Timeline", ""]
    for probe in run_state.probes:
        detail = f" ({probe.detail})" if probe.detail else ""
        lines.append(
            f"- {probe.at.isoformat()} — {probe.label}: "
            f"{'PASS' if probe.passed else 'FAIL'}{detail}"
        )

    lines += [
        "",
        "## Follow-up",
        "",
        "(none)" if overall == "PASS" else "See probe details above.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    run_state = state.load()
    report = render(run_state)
    path = state.state_dir() / "evidence.md"
    path.write_text(report, encoding="utf-8")
    logger.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
