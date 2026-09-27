#!/usr/bin/env bash
# Compare first-attempt fragments with the checked-in expected lane x OS manifest.
set -uo pipefail

SELECTED_LANE="${SELECTED_LANE:-all}"
SELECTED_OS="${SELECTED_OS:-all}"

shopt -s nullglob
files=(artifacts/scorecard-*.json)
if [ ${#files[@]} -eq 0 ]; then
  echo "::error::no lane scorecards to merge" >&2
  echo "passed=false" >> "$GITHUB_OUTPUT"
  exit 1
fi

uv run python -m tests.e2e.baseline.scorecard merge "${files[@]}" \
  --out artifacts/scorecard.json --markdown artifacts/scorecard.md \
  --summary artifacts/gate-summary.md \
  --selected-lane "$SELECTED_LANE" --selected-os "$SELECTED_OS"
code=$?
if [ "$code" -eq 0 ]; then
  echo "passed=true" >> "$GITHUB_OUTPUT"
else
  echo "passed=false" >> "$GITHUB_OUTPUT"
fi
exit "$code"
