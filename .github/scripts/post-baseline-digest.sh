#!/usr/bin/env bash
# Posts a compact baseline report as a comment on the tested commit -- not a
# GitHub Issue (this repo doesn't use them). GitHub still delivers a mention
# notification/email to everyone named in RECIPIENTS off a commit comment,
# same as it would off an issue comment; a commit comment also needs no
# find-or-create lookup and has no open/closed state to sync, since the
# commit itself (not a persistent issue) is what each night's report hangs
# off of.
# Reads REPO / RUN_URL / PASSED / MATRIX_OK / SHA / REPORT_LABEL / RECIPIENTS
# and optional SCOPE_NOTICE / RUN_LINK_LABEL from the environment.
#
# PASSED defaults to false rather than requiring it — see merge-scorecard.sh's
# EXPECTED_LANES comment for the shared "default to red instead of aborting" policy
# this is one link in (silence here would mean the commit never gets a report at
# all). MATRIX_OK stays required: it is a GitHub Actions expression on `needs.e2e`,
# always populated as long as that job exists.
set -euo pipefail

: "${REPO:?REPO is required}"
: "${RUN_URL:?RUN_URL is required}"
PASSED="${PASSED:-false}"
: "${MATRIX_OK:?MATRIX_OK is required}"
: "${SHA:?SHA is required}"
: "${REPORT_LABEL:?REPORT_LABEL is required}"
: "${RECIPIENTS:?RECIPIENTS is required}"

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

gh api "repos/$REPO/commits/$SHA/comments" -F body="@$body_file"
