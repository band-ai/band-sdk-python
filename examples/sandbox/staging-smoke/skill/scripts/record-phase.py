"""
Record a phase transition into `.sandbox-smoke/state.json`, printing that
phase's operator-facing status message (`state.PHASE_MESSAGES`, the single
source of truth). The skill calls this at each checkpoint instead of writing
to state.json directly, so the schema and the phase-to-message mapping live
in one place.

Usage:
    record-phase.py <phase>

The very first call (typically `record-phase.py started`) creates
`.sandbox-smoke/state.json` — or rotates a finished/stale previous run to a
fresh one (`state.load_or_create`); every later call updates the current run.
This is also safe to call after `probe.py --label provision` has already
created the state (the plain operator workflow in README.md mostly doesn't
use this script) — whichever runs first mints the run_id, the other reuses it.
"""

from __future__ import annotations

import logging
import sys

import root  # noqa: F401  (bootstraps sys.path as a side effect)

import state

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in state.PHASE_MESSAGES:
        valid = ", ".join(state.PHASE_MESSAGES)
        logger.error("Usage: record-phase.py <%s>", valid)
        return 1

    phase = sys.argv[1]
    run_state = state.load_or_create()
    run_state.phase = phase  # type: ignore[assignment]  # validated by the membership check above
    state.save(run_state)
    logger.info(state.PHASE_MESSAGES[phase])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
