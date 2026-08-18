#!/usr/bin/env python
"""Emit this run's lane x OS matrix `include` list, derived from the adapter registry.

Reads SELECTED_LANE / SELECTED_OS from the environment (the workflow_dispatch
inputs, already defaulted to 'all' by the caller) and prints `lanes=<json>` to
stdout for the caller to redirect into $GITHUB_OUTPUT.
"""

from __future__ import annotations

import json
import os

from tests.e2e.baseline.toolkit.adapters import assert_registry_covers_discovered
from tests.e2e.baseline.toolkit.ci_lanes import (
    assert_every_adapter_has_a_ci_home,
    assert_workflow_lane_gates_known,
    ci_lanes,
)

# Fail before any test runs if the registry or lane partition has drifted.
assert_registry_covers_discovered()
assert_every_adapter_has_a_ci_home()
# Fail if a `matrix.lane ==` gate in e2e.yml names a lane the registry no longer
# emits (its step would silently never run).
assert_workflow_lane_gates_known()

lanes = list(ci_lanes())

# The registry stays authoritative: a chosen lane must be one it emits, so a
# stale dropdown option fails loudly here instead of running nothing.
selected = os.environ.get("SELECTED_LANE") or "all"
known = {lane.id for lane in lanes}
if selected != "all" and selected not in known:
    raise SystemExit(
        f"Requested lane {selected!r} is not one the registry emits "
        f"(known: {sorted(known)}). Pick 'all' or a known lane."
    )

# OS is a workflow concern (runner image), not a registry fact: every lane runs
# on each selected OS. `runner` is the runs-on image; `os` is the short id
# surfaced in the job name.
runners = {"ubuntu": "ubuntu-latest", "windows": "windows-latest"}
selected_os = os.environ.get("SELECTED_OS") or "all"
if selected_os != "all" and selected_os not in runners:
    raise SystemExit(
        f"Requested os {selected_os!r} is not known "
        f"(known: {sorted(runners)}). Pick 'all', 'ubuntu', or 'windows'."
    )
oses = list(runners) if selected_os == "all" else [selected_os]

# A lane that can only run on Linux (its backend has no Windows story --
# LINUX_ONLY_LANES, surfaced as lane.linux_only) contributes no windows cell, so
# a full-matrix dispatch never emits a cell that can't run.
include = [
    {"lane": lane.id, "extra": lane.extra, "os": osid, "runner": runners[osid]}
    for lane in lanes
    if selected == "all" or lane.id == selected
    for osid in oses
    if not (osid == "windows" and lane.linux_only)
]

# A selection can be individually valid yet jointly empty -- a Linux-only lane asked
# for on Windows is the reachable case (both are offered by the dispatch dropdowns).
# Fail here rather than emit `include: []`: an empty matrix leaves EXPECTED_LANES
# blank downstream, which trips merge-scorecard.sh's `:?` guard and reports the run
# as a red digest, blaming the suite for what is really an impossible request.
if not include:
    linux_only = sorted(str(lane.id) for lane in lanes if lane.linux_only)
    raise SystemExit(
        f"Lane {selected!r} on os {selected_os!r} selects no runnable cell: "
        f"these lanes are Linux-only ({linux_only}). Re-dispatch with "
        "os 'ubuntu' or 'all'."
    )

print("lanes=" + json.dumps({"include": include}))
