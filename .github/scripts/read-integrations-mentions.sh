#!/usr/bin/env bash
# Turns .github/integrations-team.txt (one GitHub username per line) into
# @mention handles for the nightly baseline digest. Writes
# `mentions=<space-separated @handles>` to $GITHUB_OUTPUT.
set -euo pipefail

FILE=".github/integrations-team.txt"
if [ ! -f "$FILE" ]; then
  echo "::error::$FILE is missing — the nightly digest has no roster to cc."
  exit 1
fi

# `|| true`: grep exits 1 when it selects nothing, which for this filter means "every
# line is a comment or blank" — the empty-roster case the guard below exists to
# report. Without it, `set -e` aborts the script at the pipeline and that guard is
# unreachable, so an empty roster fails silently with no diagnostic at all.
mentions=$(grep -vE '^\s*(#|$)' "$FILE" | sed 's/^/@/' | paste -sd ' ' - || true)
if [ -z "$mentions" ]; then
  echo "::error::$FILE has no usernames — the nightly digest would have nothing to cc."
  exit 1
fi

echo "mentions=$mentions" >> "$GITHUB_OUTPUT"
