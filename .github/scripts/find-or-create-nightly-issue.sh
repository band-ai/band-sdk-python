#!/usr/bin/env bash
# One persistent issue (tracked by title, not a label — no pre-provisioned label to
# keep in sync) is the running nightly digest. Reads REPO / INTEGRATIONS_MENTIONS
# (may be unset if the mention-resolution step failed) from the environment;
# writes `number=<n>` to $GITHUB_OUTPUT.
set -euo pipefail

: "${REPO:?REPO is required}"

# --search is a fuzzy match (any issue whose title merely contains these words);
# the select() re-filters for an exact title so an unrelated issue can never get
# picked up. `// empty` (not the default `null`) is required — jq -r on a missing
# .[0].number prints the literal string "null", which `[ -z ... ]` below would not
# treat as empty.
number=$(gh issue list --repo "$REPO" --state all \
  --search 'Nightly baseline results in:title' --json number,title \
  --jq '[.[] | select(.title == "Nightly baseline results")][0].number // empty')

if [ -z "$number" ]; then
  body="Running log of nightly baseline E2E results."
  if [ -n "${INTEGRATIONS_MENTIONS:-}" ]; then
    body="$body $INTEGRATIONS_MENTIONS"
  fi
  url=$(gh issue create --repo "$REPO" --title "Nightly baseline results" --body "$body")
  number=${url##*/}
fi

echo "number=$number" >> "$GITHUB_OUTPUT"
