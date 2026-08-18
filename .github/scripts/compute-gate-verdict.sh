#!/usr/bin/env bash
# Combines the per-cell merge gate with a backstop the cell-level grid can't see on
# its own: a whole OS leg of an expected lane failing, crashing, or timing out
# (e2e.yml's e2e job caps each leg at timeout-minutes) before it writes any
# scorecard fragment at all (ScorecardRow carries no OS dimension, so a passing
# same-lane different-OS leg could otherwise hide it). GitHub reports a
# timeout-minutes kill as conclusion "cancelled", not "failure" — verified live —
# but $MATRIX_OK only checks equality with "success", so either value already
# reddens this correctly with no extra branching needed. Reads MERGE_OK /
# MATRIX_OK / E2E_RESULT from the environment; writes `ok=true|false` to
# $GITHUB_OUTPUT.
#
# MERGE_OK defaults to false rather than requiring it — see merge-scorecard.sh's
# EXPECTED_LANES comment for the shared "default to red instead of aborting" policy
# this is one link in. MATRIX_OK/E2E_RESULT are GitHub Actions expressions on
# `needs.e2e`, always populated as long as that job exists in the workflow, so they
# stay required — an empty value there is a workflow-authoring bug, not a
# legitimate empty state.
set -uo pipefail

MERGE_OK="${MERGE_OK:-false}"
: "${MATRIX_OK:?MATRIX_OK is required}"
: "${E2E_RESULT:?E2E_RESULT is required}"

if [ "$MATRIX_OK" != "true" ]; then
  echo "::error::e2e matrix result was '$E2E_RESULT' — at least one lane/OS leg failed, crashed, or timed out (see the e2e job for which and why)."
fi
if [ "$MERGE_OK" != "true" ]; then
  echo "::error::scorecard gate failed — see the step summary above for failing cells."
fi

if [ "$MERGE_OK" = "true" ] && [ "$MATRIX_OK" = "true" ]; then
  echo "ok=true" >> "$GITHUB_OUTPUT"
else
  echo "ok=false" >> "$GITHUB_OUTPUT"
fi
