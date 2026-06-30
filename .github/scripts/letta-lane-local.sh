#!/usr/bin/env bash
# Run the `letta` e2e lane locally: bring up the same letta + band-mcp stack CI
# uses (via setup-letta.sh), run the Letta smokes against it, and tear the stack
# down on exit. Mirrors the CI lane so a local pass means a CI pass.
#
# Prerequisites:
#   - `docker login ghcr.io` once (the band-mcp image is private; your account
#     must have read access — the CI built-in token isn't available locally).
#   - .env.test at the repo root providing the same vars the lane needs:
#       BAND_API_KEY        the agent band-mcp runs as (and the agent under test)
#       BAND_API_KEY_USER   the driver/observer user key
#       BAND_REST_URL       Band REST endpoint (must be reachable from a container;
#                           for a local Band dev server use host.docker.internal)
#       BAND_WS_URL         Band WebSocket endpoint
#       OPENAI_API_KEY      Letta's model
#       ANTHROPIC_API_KEY   the baseline judge
#
# Usage:  .github/scripts/letta-lane-local.sh [extra pytest args]
#   e.g.  .github/scripts/letta-lane-local.sh -k recall
set -euo pipefail
cd "$(dirname "$0")/../.."

[ -f .env.test ] || {
  echo ".env.test not found at repo root (needed for BAND_API_KEY etc.)" >&2
  exit 1
}
# Export .env.test into this shell so setup-letta.sh (a separate process that does
# not read .env.test) sees BAND_API_KEY / OPENAI_API_KEY / BAND_REST_URL.
set -a
# shellcheck disable=SC1091
source .env.test
set +a

cleanup() {
  docker rm -f letta band-mcp >/dev/null 2>&1 || true
  docker network rm band-e2e >/dev/null 2>&1 || true
}
trap cleanup EXIT
cleanup  # clear any leftovers from a previous run

# Bring up letta + band-mcp (no GITHUB_TOKEN locally -> setup-letta.sh skips the
# ghcr login and relies on your prior `docker login ghcr.io`).
.github/scripts/setup-letta.sh

LETTA_BASE_URL=http://localhost:8283 \
MCP_SERVER_URL=http://band-mcp:8002/sse \
BAND_E2E_LANE=letta E2E_TESTS_ENABLED=true \
  uv run pytest tests/e2e/baseline/smoke/adapters/test_letta.py -v -s --no-cov "$@"
