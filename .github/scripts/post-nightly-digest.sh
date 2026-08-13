#!/usr/bin/env bash
# A polished, email-safe digest — not the wide adapter x test grid (that stays in
# the step summary + artifact, where it renders full-width and looks fine). The
# header is built from $PASSED (the job's combined verdict, including the
# matrix-leg backstop), never from the cell-level digest file alone: a leg that
# crashed outright and reported nothing can flip the real verdict to red in a way
# the cell grid can't see (ScorecardRow carries no OS dimension) — so if that
# happened, say so explicitly rather than let the digest quietly disagree with the
# baseline-green commit status posted earlier in this job.
#
# Reads REPO / RUN_URL / PASSED / MATRIX_OK / SHA / ISSUE_NUMBER /
# INTEGRATIONS_MENTIONS from the environment.
set -euo pipefail

: "${REPO:?REPO is required}"
: "${RUN_URL:?RUN_URL is required}"
: "${PASSED:?PASSED is required}"
: "${MATRIX_OK:?MATRIX_OK is required}"
: "${SHA:?SHA is required}"
: "${ISSUE_NUMBER:?ISSUE_NUMBER is required}"
: "${INTEGRATIONS_MENTIONS:?INTEGRATIONS_MENTIONS is required}"

body_file="$(mktemp)"
{
  if [ "$PASSED" = "true" ]; then
    echo "🟢 **Nightly baseline: PASS**"
  else
    echo "🔴 **Nightly baseline: FAIL**"
  fi
  echo
  if [ "$MATRIX_OK" != "true" ]; then
    echo "⚠️ At least one lane/OS leg crashed outright and reported nothing (see the e2e job) — the counts below only cover what actually reported."
    echo
  fi
  if [ -f artifacts/gate-summary.md ]; then
    cat artifacts/gate-summary.md
  else
    echo "_No scorecard was produced this run — every lane failed before session end._"
  fi
  echo
  echo "Commit \`$SHA\` · [full grid & logs]($RUN_URL)"
  echo
  echo "cc $INTEGRATIONS_MENTIONS"
} > "$body_file"

gh issue comment "$ISSUE_NUMBER" --repo "$REPO" --body-file "$body_file"

if [ "$PASSED" = "true" ]; then
  gh issue close "$ISSUE_NUMBER" --repo "$REPO" 2>/dev/null || true
else
  gh issue reopen "$ISSUE_NUMBER" --repo "$REPO" 2>/dev/null || true
fi
