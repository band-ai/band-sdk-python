#!/usr/bin/env bash
# Finds one exact-title baseline reporting issue or creates it. Reads REPO /
# ISSUE_TITLE / ISSUE_BODY; writes number=<n> to $GITHUB_OUTPUT.
set -euo pipefail

: "${REPO:?REPO is required}"
: "${ISSUE_TITLE:?ISSUE_TITLE is required}"
: "${ISSUE_BODY:?ISSUE_BODY is required}"

number=$(gh issue list --repo "$REPO" --state all \
  --search "$ISSUE_TITLE in:title" --json number,title |
  jq -r --arg title "$ISSUE_TITLE" \
    '[.[] | select(.title == $title)][0].number // empty')

if [ -z "$number" ]; then
  url=$(gh issue create --repo "$REPO" --title "$ISSUE_TITLE" --body "$ISSUE_BODY")
  number=${url##*/}
fi

echo "number=$number" >> "$GITHUB_OUTPUT"
