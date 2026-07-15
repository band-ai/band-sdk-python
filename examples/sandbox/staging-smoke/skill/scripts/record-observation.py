"""
Record a behavioral observation into `.sandbox-smoke/state.json` — how a
recovery happened (survival, auto-resume or not, what recovery took), which
the evidence report prints alongside the probe verdicts. The skill calls this
instead of mutating state.json inline, for the same reason `record-phase.py`
exists: the schema and the legal check names live in one place
(`state.OBSERVATION_CHECKS`), and free text passes through as an argument
instead of being spliced into quoted Python inside quoted shell.

Usage:
    record-observation.py <check> <observation text...>
"""

from __future__ import annotations

import logging
import sys

import root  # noqa: F401  (bootstraps sys.path as a side effect)

import state

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in state.OBSERVATION_CHECKS:
        valid = ", ".join(state.OBSERVATION_CHECKS)
        logger.error("Usage: record-observation.py <%s> <observation text...>", valid)
        return 1

    check = sys.argv[1]
    observation = " ".join(sys.argv[2:])
    run_state = state.load()
    run_state.residual_checks[check] = observation
    state.save(run_state)
    logger.info("Recorded %s observation.", check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
