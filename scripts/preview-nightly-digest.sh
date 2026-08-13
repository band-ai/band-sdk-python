#!/usr/bin/env bash
# Posts a real nightly-digest comment to the tracking issue in seconds, without
# waiting on a full E2E run: fabricates a small scorecard, merges it through the
# real scorecard.py code path, then drives the exact same
# .github/scripts/{read-integrations-mentions,find-or-create-nightly-issue,
# post-nightly-digest}.sh chain e2e.yml's mark-baseline job uses — so the
# formatting/rendering preview can never drift from what CI actually posts.
#
# Usage: scripts/preview-nightly-digest.sh [pass|fail]  (default: pass)
#
# Needs `gh` authenticated with issues:write on this repo. Posts for real —
# reopens/closes the actual "Nightly baseline results" issue and pings
# INTEGRATIONS_MENTIONS, so use sparingly and expect the resulting comment to
# say so explicitly.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

mode="${1:-pass}"
case "$mode" in
  pass) passed=true ;;
  fail) passed=false ;;
  *) echo "usage: $0 [pass|fail]" >&2; exit 1 ;;
esac

work="$(mktemp -d)"
mkdir -p artifacts
trap 'rm -rf "$work" artifacts/gate-summary.md artifacts/scorecard.json artifacts/scorecard.md' EXIT

if [ "$passed" = true ]; then
  cat > "$work/scorecard-core-ubuntu.json" <<'EOF'
[
  {"test": "tests/e2e/baseline/smoke/behavior/test_isolation.py::test_tool_calls_isolated_per_room", "adapter": "anthropic", "status": "pass"},
  {"test": "tests/e2e/baseline/smoke/behavior/test_isolation.py::test_tool_calls_isolated_per_room", "adapter": "langgraph", "status": "pass"}
]
EOF
else
  cat > "$work/scorecard-core-ubuntu.json" <<'EOF'
[
  {"test": "tests/e2e/baseline/smoke/behavior/test_isolation.py::test_tool_calls_isolated_per_room", "adapter": "anthropic", "status": "pass"},
  {"test": "tests/e2e/baseline/smoke/behavior/test_multi_agent_collaboration.py::test_coordinator_delegates_to_two_specialists", "adapter": "anthropic", "status": "fail"}
]
EOF
fi

uv run python -m tests.e2e.baseline.scorecard merge "$work/scorecard-core-ubuntu.json" \
  --out artifacts/scorecard.json --markdown artifacts/scorecard.md \
  --summary artifacts/gate-summary.md --expected-lanes core || true

gh_token="$(gh auth token)"
repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
sha="$(git rev-parse HEAD)"
export GH_TOKEN="$gh_token"
export REPO="$repo"
export RUN_URL="local preview — scripts/preview-nightly-digest.sh, not a real run"
export SHA="$sha"
export PASSED="$passed"
export MATRIX_OK=true
export GITHUB_OUTPUT="$work/gh_output.txt"

: > "$GITHUB_OUTPUT"
.github/scripts/read-integrations-mentions.sh
mentions="$(grep '^mentions=' "$GITHUB_OUTPUT" | cut -d= -f2-)"
export INTEGRATIONS_MENTIONS="$mentions"

: > "$GITHUB_OUTPUT"
.github/scripts/find-or-create-nightly-issue.sh
issue_number="$(grep '^number=' "$GITHUB_OUTPUT" | cut -d= -f2-)"
export ISSUE_NUMBER="$issue_number"

.github/scripts/post-nightly-digest.sh
