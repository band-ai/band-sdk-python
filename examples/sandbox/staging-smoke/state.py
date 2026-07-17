"""
Shared, gitignored run-state for the Docker Sandbox staging smoke.

One `SmokeState` record lives at `.sandbox-smoke/state.json` for the lifetime
of a single operator run, persisted across the run's operator checkpoints —
the processes exit at the Wi-Fi and sleep/wake handoffs and during the
daemon-restart recovery, so state must survive on disk. `probe.py` and the
`skill/scripts/*.py` orchestration both read/write through this module so the
schema and file location live in exactly one place.

Never stores secrets: only ids, names, timestamps, and phase/result fields.
Each step re-reads credentials (BAND_API_KEY_USER, the provisioned agent's
api_key) from the approved secret source instead of persisting them here.

Ownership: `phase` is written by `record-phase.py`, plus exactly one write in
`probe.py` — terminal `cleanup` marks `completed`, the shared end-of-run for
the plain operator workflow, which never calls `record-phase.py`. `probes`
and the resource ids are written by `probe.py`; `residual_checks` by
`record-observation.py`. The report verdict is derived from `probes`, never
from `phase` (see `render-report.py`), so a stale or mid-run phase can never
fabricate a result.

This module (like every other file in this directory except `agent.py`) has no
PEP 723 header: it's imported by scripts that run inside this repository's own
dev environment (`uv sync --extra dev`), not executed standalone, and it needs
no third-party dependency beyond what that environment already provides.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, get_args

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Every phase is load-bearing — a checkpoint breadcrumb, a recovery marker,
# or a terminal state; progress narration is the orchestrator's job, not the
# state machine's:
#   started                 — the run exists (rotation boundary for a new run)
#   awaiting-wifi-recovery  — operator checkpoint; resume instruction
#   awaiting-sleep-wake     — operator checkpoint; resume instruction
#   daemon-restart          — marks the one mid-run case where re-provisioning
#                             under the same run is legitimate (begin_provision)
#   completed / failed      — terminal; the next entry point starts a fresh run
Phase = Literal[
    "started",
    "awaiting-wifi-recovery",
    "awaiting-sleep-wake",
    "daemon-restart",
    "completed",
    "failed",
]

# Phases that end a run. A later entry point must start a fresh run rather
# than silently continue a finished one (see load_or_create).
TERMINAL_PHASES: frozenset[str] = frozenset({"completed", "failed"})

# An in-flight run older than this is treated as abandoned, not resumable:
# rotating to a fresh run keeps its stale probes from leaking into a new
# report. Generous enough for a long lunch mid-checkpoint, far shorter than
# "operator came back next week".
MAX_RESUMABLE_AGE = timedelta(hours=12)

# The behavioral observations the full run must record (written via
# record-observation.py, rendered by render-report.py — both reference this
# list, so a check can't be named in one place and not the other).
OBSERVATION_CHECKS: tuple[str, ...] = ("sleep_wake", "daemon_restart")

# The one required status message per phase — the single source of truth
# `record-phase.py` prints from, so a phase can never be added without its
# operator-facing message (enforced by the assert below).
PHASE_MESSAGES: dict[Phase, str] = {
    "started": "Starting the sandbox staging smoke.",
    "awaiting-wifi-recovery": (
        "Turn Wi-Fi off, wait for the agent log to show disconnect, turn it "
        "back on, wait for reconnect, then ask to resume this run."
    ),
    "awaiting-sleep-wake": (
        "Put the host to sleep for about a minute, wake it, then ask to "
        "resume this run."
    ),
    "daemon-restart": (
        "Restarting the sandbox daemon; expect the agent process to die and "
        "be re-provisioned."
    ),
    "completed": "Smoke complete; see .sandbox-smoke/evidence.md.",
    "failed": "Smoke failed; see .sandbox-smoke/evidence.md for details.",
}
assert set(PHASE_MESSAGES) == set(get_args(Phase)), (
    "PHASE_MESSAGES and Phase have drifted — every phase needs exactly one message"
)


class ProbeResult(BaseModel):
    label: str
    marker: str
    passed: bool
    detail: str = ""
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SmokeState(BaseModel):
    """Non-secret run state persisted across the run's operator checkpoints."""

    run_id: str
    phase: Phase = "started"
    sandbox_name: str = ""
    sbx_version: str = ""
    sdk_version: str = ""
    room_id: str = ""
    agent_id: str = ""
    agent_name: str = ""
    probes: list[ProbeResult] = Field(default_factory=list)
    # Free-text behavioral observations, one per OBSERVATION_CHECKS entry
    # (how a recovery happened — not a verdict; the probes are the verdicts).
    residual_checks: dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def latest(self, label: str) -> ProbeResult | None:
        """The most recent attempt for ``label`` — a probe may be retried after
        a transient failure, and only the latest attempt should count toward
        PASS/FAIL (earlier failed attempts are history, not the verdict)."""
        matching = [p for p in self.probes if p.label == label]
        return matching[-1] if matching else None

    def is_resumable(self) -> bool:
        """True when a new entry point should continue this run rather than
        start a fresh one: the run is neither finished nor abandoned. Without
        this boundary, a later run would inherit this run's probes and could
        render a report containing results that were never re-proven."""
        if self.phase in TERMINAL_PHASES:
            return False
        updated = self.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - updated < MAX_RESUMABLE_AGE


def root_dir() -> Path:
    return Path(__file__).resolve().parent


def repo_root() -> Path:
    """The band-sdk-python repo root.

    Fixed by this example's own location in the tree
    (`examples/sandbox/staging-smoke/`, always exactly two directories below
    the repo root) — not a generic walk-up-for-a-marker search, which would
    solve a problem this specific, versioned directory layout doesn't have.
    """
    return root_dir().parents[2]


def state_dir() -> Path:
    return root_dir() / ".sandbox-smoke"


def state_path() -> Path:
    return state_dir() / "state.json"


def load() -> SmokeState:
    path = state_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No run state at {path}. Run setup.sh (or the skill's preflight) first."
        )
    return SmokeState.model_validate_json(path.read_text(encoding="utf-8"))


def save(run_state: SmokeState) -> None:
    """Write ``run_state`` atomically: a write killed mid-flight (Ctrl-C, the
    operator's Wi-Fi toggle) must never leave a truncated, unloadable
    state.json — the whole point of this being a resumable workflow."""
    run_state.updated_at = datetime.now(timezone.utc)
    state_dir().mkdir(parents=True, exist_ok=True)
    path = state_path()
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(run_state.model_dump_json(indent=2), encoding="utf-8")
    tmp_path.chmod(0o600)
    tmp_path.replace(path)


def new(run_id: str | None = None) -> SmokeState:
    run_state = SmokeState(run_id=run_id or uuid.uuid4().hex[:8])
    save(run_state)
    return run_state


def load_or_create() -> SmokeState:
    """Load the in-flight run, or start a fresh one.

    Two independent entry points share this: `record-phase.py started` (the
    AI-orchestrated skill workflow's first step) and `probe.py --label
    provision` (the plain operator workflow's first Band-touching step, which
    never calls `record-phase.py` at all). Whichever runs first creates the
    state; the other reuses it, so both workflows share one run_id.

    A finished or abandoned run is **not** reused (`SmokeState.is_resumable`):
    its file is archived beside state.json (evidence preserved, never
    silently continued) and a fresh run begins. Only a fresh, in-flight run —
    e.g. the daemon-restart recovery re-provision — resumes.
    """
    if not state_path().exists():
        return new()
    existing = load()
    if existing.is_resumable():
        return existing
    return _rotate(existing)


def begin_provision() -> SmokeState:
    """The run a `probe.py --label provision` should provision into.

    Reuses the current run only when it can't fabricate evidence: either no
    probes have been recorded yet (the normal first provision, or a retry
    after a crashed launch), or the workflow explicitly marked a
    daemon-restart recovery (`phase == "daemon-restart"`, set right before
    the daemon bounce — the one mid-run case where re-provisioning under the
    same run is correct). Any other pre-existing state with recorded probes
    is archived and a fresh run begins, so a new smoke can never inherit a
    previous run's PASS rows.
    """
    run_state = load_or_create()
    if run_state.probes and run_state.phase != "daemon-restart":
        return _rotate(run_state)
    return run_state


def _rotate(existing: SmokeState) -> SmokeState:
    """Archive ``existing``'s file beside state.json and start a fresh run —
    evidence is preserved, never silently continued.

    Resource ids the old run never reaped (a failed or abandoned run whose
    cleanup never ran) are carried into the fresh run, not dropped with the
    archive: the current state.json is the only record the reap paths read,
    and nothing else ever deletes a room (the orphan sweep covers only aged
    agents). The next `provision` reaps carried ids before provisioning
    replacements, and `cleanup` retries them. A cleanly finished run carries
    nothing — cleanup clears its ids before marking it terminal.
    """
    archive = state_dir() / f"state.{existing.run_id}.json"
    state_path().replace(archive)
    logger.info(
        "Previous run %s is %s; archived to %s and starting a fresh run",
        existing.run_id,
        "finished" if existing.phase in TERMINAL_PHASES else "stale or superseded",
        archive,
    )
    fresh = new()
    if existing.room_id or existing.agent_id:
        fresh.room_id = existing.room_id
        fresh.agent_id = existing.agent_id
        fresh.agent_name = existing.agent_name
        save(fresh)
        logger.info(
            "Run %s left unreaped resources (room=%s, agent=%s); carried "
            "into the new run for the next provision/cleanup to reap",
            existing.run_id,
            existing.room_id or "-",
            existing.agent_id or "-",
        )
    return fresh
