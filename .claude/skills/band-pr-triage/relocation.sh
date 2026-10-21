#!/usr/bin/env bash
# Usage: relocation.sh <base> <head>   (run inside a repo that has both refs)
# Prints how much of the diff is moved code and whether the set of test
# function names is unchanged. Read-only; evidence for triage, not a verdict.
set -euo pipefail
base=$(git merge-base "$1" "$2")
head=$2

# Pin the colours so the counts do not depend on the user's git config.
git -c color.diff.old=red -c color.diff.new=green \
  -c color.diff.oldMoved=magenta -c color.diff.newMoved=cyan \
  -c color.diff.oldMovedAlternative=blue -c color.diff.newMovedAlternative=yellow \
  diff --color=always --color-moved=zebra --color-moved-ws=allow-indentation-change "$base" "$head" |
  awk -v esc="$(printf '\033')" '
    $0 ~ "^" esc "\\[(1;)?3[3-6]m[-+]" { moved++; next }
    $0 ~ "^" esc "\\[3[12]m[-+]" { changed++ }
    END { printf "moved lines: %d\nchanged lines (not moved): %d\n", moved, changed }'

names() {
  git grep -hE '^[[:space:]]*(async )?def test_[A-Za-z0-9_]+' "$1" -- tests |
    sed -E 's/^[[:space:]]*(async )?def //; s/\(.*//' | sort
}
if diff <(names "$base") <(names "$head") >/dev/null; then
  echo "test function names: identical ($(names "$base" | wc -l | tr -d ' ') tests)"
else
  echo "test function names: DIFFER (added/removed: $(diff <(names "$base") <(names "$head") | grep -c '^[<>]'))"
fi
