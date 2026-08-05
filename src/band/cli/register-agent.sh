#!/usr/bin/env bash
#
# Register a Band external agent from a user API key, using only curl + a JSON
# parser. A portable, dependency-light alternative to the Hermes plugin's
# hermes_band_platform/skills/add-band/scripts/register_agent.py: no Python SDK,
# no hermes_cli, no cloned repo — so a bootstrap can mint a Band agent before it
# installs anything (the same shape openclaw/bootstrap.sh uses).
#
# Security: the user key is read from $BAND_USER_API_KEY (never an argument) and
# handed to curl through a --config heredoc on stdin, so it never appears in any
# process's argv (`ps`). Only the returned agent-scoped id + key are printed; the
# user key is never echoed.
#
# Output (stdout — capture- / eval-able):
#   BAND_AGENT_ID=<uuid>
#   BAND_API_KEY=<agent-key>
#
# Usage (bundled with the SDK as the `band-register-agent` command):
#   export BAND_USER_API_KEY=...        # required
#   eval "$(band-register-agent)"       # sets BAND_AGENT_ID + BAND_API_KEY
#
# Env knobs: BAND_BASE_URL (default https://app.band.ai),
#            BAND_AGENT_NAME, BAND_AGENT_DESCRIPTION.
set -euo pipefail

base="${BAND_BASE_URL:-https://app.band.ai}"; base="${base%/}"
name="${BAND_AGENT_NAME:-Band agent}"
desc="${BAND_AGENT_DESCRIPTION:-Agent on Band}"
: "${BAND_USER_API_KEY:?set BAND_USER_API_KEY to a Band user API key with agent-create scope}"

req_body=$(printf '{"agent":{"name":"%s","description":"%s"}}' "$name" "$desc")

# Only the secret X-API-Key header goes through stdin (-K -), never argv.
resp=$(curl -sS -X POST "$base/api/v1/me/agents/register" \
  -H "Content-Type: application/json" -d "$req_body" -w $'\n%{http_code}' -K - <<EOF
header = "X-API-Key: $BAND_USER_API_KEY"
EOF
) || true

code=${resp##*$'\n'}
out=${resp%$'\n'*}
case "$code" in
  200 | 201) ;;
  *) echo "band: registration failed (HTTP ${code:-?}): $(printf '%.300s' "$out")" >&2; exit 1 ;;
esac

# Pull the agent id + key from the response shapes Band may return.
if command -v jq >/dev/null 2>&1; then
  id=$(printf '%s' "$out" | jq -r '.data.agent.id // .agent.id // .data.id // .agent_id // .id // empty')
  key=$(printf '%s' "$out" | jq -r '.data.credentials.api_key // .credentials.api_key // .data.api_key // .api_key // .key // .token // empty')
elif command -v python3 >/dev/null 2>&1; then
  read -r id key < <(printf '%s' "$out" | python3 -c '
import sys, json
d = json.load(sys.stdin)
def g(*path):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur
i = g("data","agent","id") or g("agent","id") or g("data","id") or d.get("agent_id") or d.get("id") or ""
k = g("data","credentials","api_key") or g("credentials","api_key") or g("data","api_key") or d.get("api_key") or d.get("key") or d.get("token") or ""
print(str(i).strip(), str(k).strip())
')
else
  echo "band: need jq or python3 to parse the registration response" >&2
  exit 1
fi

[ -n "${id:-}" ] && [ -n "${key:-}" ] || {
  echo "band: registration response missing agent id/key" >&2
  exit 1
}

printf 'BAND_AGENT_ID=%s\nBAND_API_KEY=%s\n' "$id" "$key"
