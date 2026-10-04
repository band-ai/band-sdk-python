#!/usr/bin/env bash
# One room's container: band-mcp on the container's loopback, then Copilot's ACP
# server on this process's stdio. stdout IS the ACP channel, so nothing else may
# write to it — band-mcp's output goes to stderr.
set -euo pipefail

: "${GITHUB_TOKEN:?set GITHUB_TOKEN (Copilot-entitled)}"
: "${BAND_AGENT_KEY:?set BAND_AGENT_KEY (the Band identity band-mcp acts as)}"

# band-mcp's SSE transport rejects requests (HTTP 421) unless the caller's Host
# is allow-listed; Copilot dials localhost/127.0.0.1 here.
export ALLOWED_HOSTS='["localhost:*","127.0.0.1:*"]'
export BAND_BASE_URL="${BAND_REST_URL:-https://app.band.ai}"

/opt/band-mcp/bin/band-mcp --transport sse --host 127.0.0.1 --port 3000 >&2 &

# Copilot opens its MCP connection while creating the first session, so band-mcp
# must already be listening.
for _ in $(seq 1 50); do
  (exec 3<>/dev/tcp/127.0.0.1/3000) 2>/dev/null && break
  sleep 0.2
done

# --allow-all-tools lets Copilot's built-in tools run unattended inside this
# throwaway container (Band/MCP tools are approved through ACP); drop it to gate
# built-in shell/file tools. Enterprise policy can disable allow-all flags.
exec copilot --acp --allow-all-tools
