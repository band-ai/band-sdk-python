#!/usr/bin/env bash
# Turns .github/integrations-team.txt (one GitHub username per line) into
# @mention handles for the nightly baseline digest. Writes
# `mentions=<space-separated @handles>` to $GITHUB_OUTPUT.
set -euo pipefail

FILE=".github/integrations-team.txt"
mentions=$(grep -vE '^\s*(#|$)' "$FILE" | sed 's/^/@/' | paste -sd ' ' -)
if [ -z "$mentions" ]; then
  echo "::error::$FILE has no usernames — the nightly digest would have nothing to cc."
  exit 1
fi

echo "mentions=$mentions" >> "$GITHUB_OUTPUT"
