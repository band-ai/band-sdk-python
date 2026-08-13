#!/usr/bin/env bash
# Resolves the current band-ai/integrations team roster into @mention handles for
# the nightly baseline digest (see e2e.yml's mark-baseline job). Individual
# @username mentions, not the @band-ai/integrations team handle — verified live
# that a bot-authored (secrets.GITHUB_TOKEN) team mention does not reliably fan
# out a per-member notification, even with every member's own "Participating/
# @mentions" setting correctly on.
#
# The default Actions GITHUB_TOKEN can't do this lookup itself: GitHub's team
# members endpoint is only available to an org-member-authenticated token with
# read:org, and a repo-scoped Actions installation token carries no org
# membership. Reads a separate PAT (read:org scope only) via $ORG_READ_TOKEN.
#
# Writes `mentions=<space-separated @handles>` to $GITHUB_OUTPUT. Run this step
# with continue-on-error: true in the workflow — it only feeds the digest's "cc"
# line, never the baseline-green commit status, so a missing/stale token must
# never block the actual notification pipeline.
set -euo pipefail

: "${ORG_READ_TOKEN:?ORG_READ_TOKEN (a PAT with read:org) is required}"
ORG="${ORG:-band-ai}"
TEAM_SLUG="${TEAM_SLUG:-integrations}"

logins=$(GH_TOKEN="$ORG_READ_TOKEN" gh api "orgs/$ORG/teams/$TEAM_SLUG/members" --jq '.[].login')
if [ -z "$logins" ]; then
  echo "::warning::orgs/$ORG/teams/$TEAM_SLUG/members returned no members — check ORG_READ_TOKEN's read:org scope and the team slug."
  exit 1
fi

mentions=$(echo "$logins" | sed 's/^/@/' | paste -sd ' ' -)
echo "mentions=$mentions" >> "$GITHUB_OUTPUT"
