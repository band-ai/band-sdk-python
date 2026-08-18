#!/usr/bin/env bash
# Posts a compact baseline report to its tracking issue. Full runs sync the issue's
# open/closed state; scoped manual runs only report their selected coverage.
# Reads REPO / RUN_URL / PASSED / MATRIX_OK / SHA / ISSUE_NUMBER / REPORT_LABEL /
# RECIPIENTS / SYNC_ISSUE_STATE and optional SCOPE_NOTICE / RUN_LINK_LABEL from the
# environment.
#
# PASSED defaults to false rather than requiring it — see merge-scorecard.sh's
# EXPECTED_LANES comment for the shared "default to red instead of aborting" policy
# this is one link in (silence here would mean the issue never reflects the run at
# all). MATRIX_OK stays required: it is a GitHub Actions expression on `needs.e2e`,
# always populated as long as that job exists.
set -euo pipefail

: "${REPO:?REPO is required}"
: "${RUN_URL:?RUN_URL is required}"
PASSED="${PASSED:-false}"
: "${MATRIX_OK:?MATRIX_OK is required}"
: "${SHA:?SHA is required}"
: "${ISSUE_NUMBER:?ISSUE_NUMBER is required}"
: "${REPORT_LABEL:?REPORT_LABEL is required}"
: "${RECIPIENTS:?RECIPIENTS is required}"
: "${SYNC_ISSUE_STATE:?SYNC_ISSUE_STATE is required}"

# Where the rendered digest body comes from. Overridable so a caller can point at its
# own scratch copy (scripts/preview-baseline-digest.sh does) instead of having to
# write into — and then clean up — the shared ./artifacts directory CI downloads into.
GATE_SUMMARY_FILE="${GATE_SUMMARY_FILE:-artifacts/gate-summary.md}"

body_file="$(mktemp)"
{
  if [ "$PASSED" = "true" ]; then
    echo "![${REPORT_LABEL}: PASS](https://img.shields.io/badge/baseline-PASS-success)"
    echo "🟢 **${REPORT_LABEL}: PASS**"
  else
    echo "![${REPORT_LABEL}: FAIL](https://img.shields.io/badge/baseline-FAIL-critical)"
    echo "🔴 **${REPORT_LABEL}: FAIL**"
  fi
  echo
  if [ -n "${SCOPE_NOTICE:-}" ]; then
    echo "⚠️ $SCOPE_NOTICE"
    echo
  fi
  if [ "$MATRIX_OK" != "true" ]; then
    # Deliberately does not restate the leg time cap: it is defined once, in
    # e2e.yml's `timeout-minutes`, and a stale copy here would misinform the very
    # people this email is meant to inform.
    echo "⚠️ At least one selected lane/OS leg failed, crashed, or hit its time cap before reporting anything — see the e2e job for which and why. The counts below only cover what actually reported."
    echo
  fi
  if [ -f "$GATE_SUMMARY_FILE" ]; then
    cat "$GATE_SUMMARY_FILE"
  else
    echo "_No scorecard was produced this run — every selected lane failed before session end._"
  fi
  echo
  echo "Commit \`$SHA\` · [${RUN_LINK_LABEL:-full grid & logs}]($RUN_URL)"
  echo
  echo "cc $RECIPIENTS"
} > "$body_file"

gh issue comment "$ISSUE_NUMBER" --repo "$REPO" --body-file "$body_file"

if [ "$SYNC_ISSUE_STATE" = "true" ]; then
  if [ "$PASSED" = "true" ]; then
    gh issue close "$ISSUE_NUMBER" --repo "$REPO" 2>/dev/null || true
  else
    gh issue reopen "$ISSUE_NUMBER" --repo "$REPO" 2>/dev/null || true
  fi
fi
