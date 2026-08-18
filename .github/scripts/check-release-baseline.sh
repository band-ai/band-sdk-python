#!/usr/bin/env bash
# Decides whether the base branch's baseline is green enough to let the standing
# release-please PR merge. Reads REPO / BASE_REF and the optional tuning knobs
# MAX_BASELINE_AGE_DAYS / COMMIT_SCAN_LIMIT from the environment.
#
# A nightly marks exactly ONE commit: whatever the base branch's tip was at its
# scheduled minute. Gating on the *live* tip would therefore go red the moment
# anything merged after that nightly, leaving the release check red almost all the
# time — a gate that is red by default teaches people to bypass it rather than to
# trust it. So this walks the branch newest-first for the most recent commit a nightly
# actually tested and gates on that verdict, bounded two ways so it keeps real teeth:
# a non-green verdict blocks, and a green one that has gone stale
# (MAX_BASELINE_AGE_DAYS) no longer vouches for today's code.
set -euo pipefail

: "${REPO:?REPO is required}"
: "${BASE_REF:?BASE_REF is required}"
MAX_BASELINE_AGE_DAYS="${MAX_BASELINE_AGE_DAYS:-7}"
# How far back to look for a tested commit. Nightlies run daily, so the last tested
# commit is only as far back as the merges since — this is generous headroom, while
# still bounding the per-commit status lookups below.
COMMIT_SCAN_LIMIT="${COMMIT_SCAN_LIMIT:-40}"

# shellcheck source=.github/scripts/baseline-status-context.sh
. "$(dirname "$0")/baseline-status-context.sh"

# Statuses come back newest-first, so `.[0]` is the latest post for this context;
# `select(. != null)` makes "no status of this context" an empty result, not "null".
jq_latest="map(select(.context == \"$BASELINE_STATUS_CONTEXT\")) | .[0]"
jq_latest="$jq_latest | select(. != null) | \"\\(.state) \\(.created_at)\""

mapfile -t shas < <(
  gh api "repos/$REPO/commits?sha=$BASE_REF&per_page=$COMMIT_SCAN_LIMIT" --jq '.[].sha'
)

entry=""
tested_sha=""
for sha in "${shas[@]}"; do
  # A real `gh api` failure (network hiccup, transient 5xx, rate limit) must abort
  # here, not be swallowed and mistaken for "no status on this commit" — that would
  # let the scan silently walk past a possibly-red (or itself-untested) commit onto
  # an older green one. Capturing the exit code separately from the output keeps
  # the legitimate "no status of this context" case (already an empty `entry` via
  # `jq_latest`'s `select(. != null)`) distinct from an unreadable one.
  rc=0
  entry=$(gh api "repos/$REPO/commits/$sha/statuses" --jq "$jq_latest") || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "::error::gh api failed reading commit statuses for $sha (exit $rc) — aborting rather than scanning past a commit whose status could not be read."
    exit 1
  fi
  if [ -n "$entry" ]; then
    tested_sha="$sha"
    break
  fi
done

if [ -z "$entry" ]; then
  echo "::error::No $BASELINE_STATUS_CONTEXT status on any of the last ${#shas[@]} commits of $BASE_REF — waiting on a nightly baseline run before the release PR can merge."
  exit 1
fi

state="${entry%% *}"
created_at="${entry#* }"
age_days=$(( ( $(date -u +%s) - $(date -u -d "$created_at" +%s) ) / 86400 ))

echo "$BASE_REF: most recent $BASELINE_STATUS_CONTEXT is '$state' on $tested_sha ($created_at, ${age_days}d ago)"

if [ "$state" != "success" ]; then
  echo "::error::The most recent baseline run on $BASE_REF was not green (status: $state on $tested_sha) — fix the baseline before releasing."
  exit 1
fi

if [ "$age_days" -gt "$MAX_BASELINE_AGE_DAYS" ]; then
  echo "::error::The most recent green baseline on $BASE_REF is ${age_days} days old (limit: ${MAX_BASELINE_AGE_DAYS}) — re-run the nightly baseline before releasing."
  exit 1
fi
