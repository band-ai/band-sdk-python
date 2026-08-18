#!/usr/bin/env bash
# Posts the baseline-green/baseline-red commit status release-gate.yml reads to
# decide whether the release-please PR may merge. Reads GH_TOKEN / REPO / RUN_URL /
# PASSED / SHA from the environment.
#
# PASSED defaults to false rather than requiring it: it is the scorecard job's
# verdict output, which is empty if that job's own gate-verdict step aborted before
# writing one. A verdict that could not be computed must still post a red status,
# not silently skip posting one (a missing status here just makes the release gate
# fall back to an older green — see check-release-baseline.sh).
set -euo pipefail

: "${REPO:?REPO is required}"
: "${RUN_URL:?RUN_URL is required}"
PASSED="${PASSED:-false}"
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
