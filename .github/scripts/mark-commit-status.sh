#!/usr/bin/env bash
# Posts the baseline-green/baseline-red commit status release-gate.yml reads to
# decide whether the release-please PR may merge. Reads GH_TOKEN / REPO / RUN_URL /
# PASSED / SHA from the environment.
set -euo pipefail

: "${REPO:?REPO is required}"
: "${RUN_URL:?RUN_URL is required}"
: "${PASSED:?PASSED is required}"
: "${SHA:?SHA is required}"

# shellcheck source=.github/scripts/baseline-status-context.sh
. "$(dirname "$0")/baseline-status-context.sh"

if [ "$PASSED" = "true" ]; then
  state=success
  description="Nightly baseline green"
else
  state=failure
  description="Nightly baseline red — see the run for failing cells"
fi

gh api "repos/$REPO/statuses/$SHA" \
  -f state="$state" -f context="$BASELINE_STATUS_CONTEXT" \
  -f description="$description" -f target_url="$RUN_URL"
