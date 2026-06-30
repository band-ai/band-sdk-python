#!/usr/bin/env bash
# Stand up the stack the `letta` e2e lane needs: a self-hosted Letta server plus
# the band-mcp server it calls to execute Band platform tools — mirroring
# examples/letta/docker-compose.yml.
#
# Both run on one Docker network so Letta reaches band-mcp by service name
# (http://band-mcp:8002/sse) — a routable host, since Letta's SSRF guard rejects
# loopback. Letta is published to the host on :8283 for the adapter (the pytest
# process) to drive; band-mcp stays on the network (only Letta calls it).
#
# Reads (from the workflow step env):
#   OPENAI_API_KEY   Letta's model (openai/gpt-4o-mini default); reused, no new secret
#   BAND_API_KEY     the agent band-mcp authenticates as (== the agent under test)
#   BAND_REST_URL    Band REST endpoint band-mcp calls
#   GITHUB_TOKEN     GHCR pull token for the private band-mcp image (Actions built-in)
#   GITHUB_ACTOR     GHCR login user
# Exports to later steps via $GITHUB_ENV:
#   LETTA_BASE_URL   http://localhost:8283
#   MCP_SERVER_URL   http://band-mcp:8002/sse (the URL the adapter registers with Letta)
set -euo pipefail

NETWORK=band-e2e
BAND_MCP_IMAGE="ghcr.io/band-ai/band-mcp:latest"

# Authenticate to GHCR for the private band-mcp image. Skipped when no token is
# present (a local run where the image is already pulled).
if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "${GITHUB_TOKEN}" | docker login ghcr.io -u "${GITHUB_ACTOR:-x}" --password-stdin
fi

docker network create "$NETWORK" 2>/dev/null || true

# Letta server. Uses OPENAI_API_KEY for its model; published on :8283 for the host.
docker run -d --name letta --network "$NETWORK" -p 8283:8283 \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" letta/letta:latest

# band-mcp: serves Band platform tools to Letta, authenticated as BAND_API_KEY's
# agent. Reachable on the network as host "band-mcp" (no host port needed).
docker run -d --name band-mcp --network "$NETWORK" \
  -e BAND_REST_URL="${BAND_REST_URL}" \
  -e BAND_API_KEY="${BAND_API_KEY}" \
  -e MCP_PORT=8002 \
  "$BAND_MCP_IMAGE"

ready=false
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8283/v1/health/; then ready=true; break; fi
  sleep 2
done
# Fail loudly here if the server never came up — otherwise the step would go green
# with a dead server and the lane would fail later with an opaque connection error.
if [ "$ready" != true ]; then
  echo "Letta server did not become healthy on :8283" >&2
  docker logs letta 2>&1 | tail -50 || true
  docker logs band-mcp 2>&1 | tail -50 || true
  exit 1
fi

# Always print the discovered URLs (so a local run can read them); also append to
# $GITHUB_ENV when running under Actions so later steps inherit them.
LETTA_BASE_URL="http://localhost:8283"
MCP_SERVER_URL="http://band-mcp:8002/sse"
echo "LETTA_BASE_URL=$LETTA_BASE_URL"
echo "MCP_SERVER_URL=$MCP_SERVER_URL"
if [ -n "${GITHUB_ENV:-}" ]; then
  echo "LETTA_BASE_URL=$LETTA_BASE_URL" >> "$GITHUB_ENV"
  echo "MCP_SERVER_URL=$MCP_SERVER_URL" >> "$GITHUB_ENV"
fi
