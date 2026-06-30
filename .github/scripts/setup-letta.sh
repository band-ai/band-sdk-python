#!/usr/bin/env bash
# Start the self-hosted Letta server for the `letta` e2e lane.
#
# Reads OPENAI_API_KEY (job env) for Letta's model and exports LETTA_BASE_URL of
# the running server to later steps via $GITHUB_ENV.
set -euo pipefail

# Auto-relay mode: the adapter registers NO Band MCP server (a self-hosted Letta
# rejects a loopback/private-IP MCP URL via its SSRF guard, and stdio MCP isn't
# registrable via its API), so the adapter relays the model's reply to the room
# itself. That means no MCP server, no --network host, no tunnel — just a plain
# Letta server reachable on :8283. Letta uses OPENAI_API_KEY for its model (the
# openai/gpt-4o-mini default); no new secret.
docker run -d --name letta -p 8283:8283 \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" letta/letta:latest
for _ in $(seq 1 60); do
  curl -fsS http://localhost:8283/v1/health/ && break
  sleep 2
done
# Self-hosted base_url makes the Letta requirement available with no cloud key.
echo "LETTA_BASE_URL=http://localhost:8283" >> "$GITHUB_ENV"
