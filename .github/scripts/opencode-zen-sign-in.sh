#!/usr/bin/env bash
# Sign in to OpenCode Zen with the job's GitHub OIDC token and export the
# short-lived key as OPENCODE_ZEN_API_KEY to later steps via $GITHUB_ENV.
#
# Reads OPENCODE_ZEN_CLIENT_ID (the console credential's public client id), the
# runner's ACTIONS_ID_TOKEN_REQUEST_URL/TOKEN (the job needs `id-token: write`)
# and GITHUB_ENV. OPENCODE_ZEN_TOKEN_URL overrides the exchange endpoint (tests).
# Each failure names its stage and prints the server's error body, which never
# carries a token.
set -euo pipefail

: "${OPENCODE_ZEN_CLIENT_ID:?OPENCODE_ZEN_CLIENT_ID is required}"
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?the job needs id-token: write}"
: "${ACTIONS_ID_TOKEN_REQUEST_TOKEN:?the job needs id-token: write}"
: "${GITHUB_ENV:?GITHUB_ENV is required}"
audience="https://opencode.ai/console"
token_url="${OPENCODE_ZEN_TOKEN_URL:-https://opencode.ai/console/auth/oauth/token}"

fail() {
  echo "OpenCode Zen sign-in failed: $1" >&2
  exit 1
}

oidc_response=$(curl -sS --fail-with-body --max-time 30 \
  --oauth2-bearer "$ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
  "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=$audience") ||
  fail "GitHub OIDC request: $oidc_response"
oidc=$(jq -er '.value // empty' <<<"$oidc_response") ||
  fail "GitHub OIDC response has no token"

response=$(curl -sS --fail-with-body --max-time 30 "$token_url" \
  -d grant_type=urn:ietf:params:oauth:grant-type:token-exchange \
  -d subject_token_type=urn:ietf:params:oauth:token-type:jwt \
  -d client_id="$OPENCODE_ZEN_CLIENT_ID" \
  -d subject_token="$oidc") ||
  fail "token exchange: $response"
token=$(jq -er '.access_token // empty' <<<"$response") ||
  fail "token exchange returned no access_token"

echo "::add-mask::$token"
echo "OpenCode Zen token expires_in=$(jq -r '.expires_in // "unknown"' <<<"$response")"
echo "OPENCODE_ZEN_API_KEY=$token" >> "$GITHUB_ENV"
