#!/usr/bin/env bash
# Folds every lane's scorecard fragment into one adapter x test grid and gates on
# it. Reads EXPECTED_LANES (the lane ids this run actually selected) from the
# environment; writes `passed=true|false` to $GITHUB_OUTPUT for the caller.
#
# EXPECTED_LANES defaults to empty rather than aborting on a missing value: an
# upstream failure (e.g. the `lanes` job producing no output) must still let this
# script reach the merge below and report red, not die before it ever writes
# `passed`. An empty value is then an unknown lane id to the module below, so
# `--expected-lanes ""` fails validation there and the run is still gated red.
set -uo pipefail

EXPECTED_LANES="${EXPECTED_LANES:-}"

shopt -s nullglob
files=(artifacts/scorecard-*.json)
if [ ${#files[@]} -eq 0 ]; then
  echo "::error::no lane scorecards to merge — every lane failed before session end"
  echo "passed=false" >> "$GITHUB_OUTPUT"
  exit 0
fi

uv run python -m tests.e2e.baseline.scorecard merge "${files[@]}" \
  --out artifacts/scorecard.json --markdown artifacts/scorecard.md \
  --summary artifacts/gate-summary.md --expected-lanes "$EXPECTED_LANES"
code=$?

if [ "$code" -eq 0 ]; then
  echo "passed=true" >> "$GITHUB_OUTPUT"
else
  echo "passed=false" >> "$GITHUB_OUTPUT"
fi
exit "$code"
