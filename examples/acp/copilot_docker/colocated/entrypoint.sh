#!/usr/bin/env bash
# Start band-mcp on the container's loopback, then expose Copilot's ACP server
# over TCP. Copilot calls Band tools at http://127.0.0.1:3000/sse (same container);
# only the ACP port (8080) is published to the host.
set -euo pipefail

: "${GITHUB_TOKEN:?set GITHUB_TOKEN (Copilot-entitled)}"
: "${BAND_AGENT_KEY:?set BAND_AGENT_KEY (the Band identity band-mcp acts as)}"

# band-mcp on loopback. Its SSE transport rejects requests (HTTP 421) unless the
# caller's Host is allow-listed; Copilot dials localhost/127.0.0.1 here.
export ALLOWED_HOSTS='["localhost:*","127.0.0.1:*"]'
export BAND_BASE_URL="${BAND_REST_URL:-https://app.band.ai}"

/opt/band-mcp/bin/band-mcp --transport sse --host 127.0.0.1 --port 3000 &
MCP_PID=$!
trap 'kill "$MCP_PID" 2>/dev/null || true' EXIT

# Expose Copilot's stdio ACP server on a routable TCP port for the host-side SDK.
# (`copilot --acp --port` binds loopback only, so socat fronts it on 0.0.0.0.)
# --allow-all-tools lets Copilot's built-in tools run unattended in this isolated
# container (MCP/Band tools are already auto-approved via the ACP handler); drop it
# to gate built-in shell/file tools. Enterprise policy can disable allow-all flags.
exec socat TCP-LISTEN:8080,fork,reuseaddr EXEC:"copilot --acp --allow-all-tools"
