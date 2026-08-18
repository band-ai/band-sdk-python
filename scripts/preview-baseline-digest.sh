#!/usr/bin/env bash
# Posts a real baseline-digest comment without waiting for E2E. The optional scope
# selects the canonical nightly/team report or a requester-only scoped manual report.
# Usage: scripts/preview-baseline-digest.sh [pass|fail] [nightly|manual]
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

mode="${1:-pass}"
scope="${2:-nightly}"
case "$mode" in
  pass) passed=true ;;
  fail) passed=false ;;
  *) echo "usage: $0 [pass|fail] [nightly|manual]" >&2; exit 1 ;;
esac
case "$scope" in
  nightly|manual) ;;
  *) echo "usage: $0 [pass|fail] [nightly|manual]" >&2; exit 1 ;;
esac

# Everything this preview writes stays inside $work, so it never touches (or, on
# cleanup, deletes) a real ./artifacts directory a developer may have downloaded a
# live run's scorecard into. post-baseline-digest.sh reads GATE_SUMMARY_FILE.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

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

export GATE_SUMMARY_FILE="$work/gate-summary.md"
uv run python -m tests.e2e.baseline.scorecard merge "$work/scorecard-core-ubuntu.json" \
  --out "$work/scorecard.json" --markdown "$work/scorecard.md" \
  --summary "$GATE_SUMMARY_FILE" --expected-lanes core || true

export GH_TOKEN="$(gh auth token)"
export REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
export RUN_URL="https://github.com/$REPO/actions"
export RUN_LINK_LABEL="Actions (preview only; no live E2E run)"
export SHA="$(git rev-parse HEAD)"
export PASSED="$passed"
export MATRIX_OK=true
export GITHUB_OUTPUT="$work/gh_output.txt"

case "$scope" in
  nightly)
    : > "$GITHUB_OUTPUT"
    .github/scripts/read-integrations-mentions.sh
    export RECIPIENTS="$(grep '^mentions=' "$GITHUB_OUTPUT" | cut -d= -f2-)"
    export ISSUE_TITLE="Nightly baseline results"
    export ISSUE_BODY="Running log of nightly baseline E2E results. $RECIPIENTS"
    export REPORT_LABEL="Nightly baseline"
    export SYNC_ISSUE_STATE=true
    ;;
  manual)
    export RECIPIENTS="@$(gh api user --jq .login)"
    export ISSUE_TITLE="Manual E2E results"
    export ISSUE_BODY="Running log of scoped manual baseline E2E results. These reports do not certify the full release baseline."
    export REPORT_LABEL="Manual E2E (scoped)"
    export SCOPE_NOTICE="Preview scope: lane=core, os=ubuntu. This is not a full baseline and does not affect the release gate or nightly baseline state."
    export SYNC_ISSUE_STATE=false
    ;;
esac

: > "$GITHUB_OUTPUT"
.github/scripts/find-or-create-tracking-issue.sh
export ISSUE_NUMBER="$(grep '^number=' "$GITHUB_OUTPUT" | cut -d= -f2-)"

.github/scripts/post-baseline-digest.sh
