#!/usr/bin/env bash
# Runs the lane's baseline E2E suite, retrying failed nodeids once before writing
# the lane's final scorecard. Flaky-prone tests already self-rerun per-test via
# flaky_model/flaky_infra (see tests/e2e/baseline/flaky.py) with an explicit,
# audited policy; this is a coarser backstop on top of that: if the first pass has
# any failure at all, rerun only the failed nodeids once (--last-failed) before the
# scorecard is finalized -- absorbing a whole-lane transient (e.g. a rate-limit
# window) without weakening detection of a real bug, which fails the rerun too.
#
# Deliberately NOT `set -e`: with it on, a first-attempt pytest failure aborts
# the script at the very next line, before `code=$?` ever captures it, silently
# killing both the retry and the copy-to-$FINAL below. This was a real, live bug
# when this logic was still inline in e2e.yml's `run: |` block, which GitHub
# Actions executes under `bash -eo pipefail` by default. `set -uo pipefail`
# keeps unset-variable and pipe-failure safety without that trap.
set -uo pipefail

: "${ATTEMPT1:?ATTEMPT1 scorecard path is required}"
: "${ATTEMPT2:?ATTEMPT2 scorecard path is required}"
: "${FINAL:?FINAL scorecard path is required}"

mkdir -p artifacts/attempts
BAND_E2E_SCORECARD_JSON="$ATTEMPT1" uv run pytest tests/e2e/baseline/ -v -s --no-cov
code=$?
if [ "$code" -ne 0 ]; then
  BAND_E2E_SCORECARD_JSON="$ATTEMPT2" uv run pytest tests/e2e/baseline/ -v -s --no-cov --last-failed
  code=$?
fi

# Each attempt writes its OWN scorecard file (under artifacts/attempts/, outside
# the artifacts/scorecard-*.json glob the merge step consumes): `--last-failed`
# restricts the retry's pytest session to only the failed nodeids, so its
# ScorecardCollector never sees -- and would otherwise silently drop on a plain
# overwrite -- every cell that passed on attempt 1. `scorecard.py overlay` folds
# the retry's rows over the original's (last attempt always wins per cell,
# regardless of pass/fail rank) into the one file the rest of the job expects.
if [ -f "$ATTEMPT2" ]; then
  uv run python -m tests.e2e.baseline.scorecard overlay "$ATTEMPT1" "$ATTEMPT2" --out "$FINAL"
elif [ -f "$ATTEMPT1" ]; then
  cp "$ATTEMPT1" "$FINAL"
fi
exit "$code"
