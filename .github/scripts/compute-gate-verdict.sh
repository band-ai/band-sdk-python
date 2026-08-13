#!/usr/bin/env bash
# Combines the per-cell merge gate with a backstop the cell-level grid can't see on
# its own: a whole OS leg of an expected lane crashing before it writes any
# scorecard fragment at all (ScorecardRow carries no OS dimension, so a passing
# same-lane different-OS leg could otherwise hide it). Reads MERGE_OK / MATRIX_OK /
# E2E_RESULT from the environment; writes `ok=true|false` to $GITHUB_OUTPUT.
set -uo pipefail

: "${MERGE_OK:?MERGE_OK is required}"
: "${MATRIX_OK:?MATRIX_OK is required}"
: "${E2E_RESULT:?E2E_RESULT is required}"

if [ "$MATRIX_OK" != "true" ]; then
  echo "::error::e2e matrix result was '$E2E_RESULT' — at least one lane/OS leg failed outright (see the e2e job)."
fi
if [ "$MERGE_OK" != "true" ]; then
  echo "::error::scorecard gate failed — see the step summary above for failing cells."
fi

if [ "$MERGE_OK" = "true" ] && [ "$MATRIX_OK" = "true" ]; then
  echo "ok=true" >> "$GITHUB_OUTPUT"
else
  echo "ok=false" >> "$GITHUB_OUTPUT"
fi
