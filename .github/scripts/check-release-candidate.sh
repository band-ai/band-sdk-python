#!/usr/bin/env bash
set -euo pipefail
head_ref="$PR_HEAD"
if [ "$EVENT_NAME" = merge_group ]; then
  number=$(echo "$QUEUE_REF" | sed -E 's#.*/pr-([0-9]+)-.*#\1#')
  [[ "$number" =~ ^[0-9]+$ ]] || { echo "Cannot identify merge queue PR" >&2; exit 1; }
  head_ref=$(gh pr view "$number" --repo "$REPO" --json headRefName --jq .headRefName)
fi
case "$head_ref" in
  release-please--branches--main*) ;;
  *) exit 0 ;;
esac
uv run --all-packages python .github/scripts/releasecheck.py --previous-ref "$BASE_SHA"
