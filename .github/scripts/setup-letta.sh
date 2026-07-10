#!/usr/bin/env bash
# Start a self-hosted Letta server (docker) for the `letta` e2e lane.
#
# Reads OPENAI_API_KEY (job env, the Letta server's own LLM provider) and
# exports LETTA_BASE_URL of the running server to later steps via $GITHUB_ENV.
#
# No persistent volume on purpose: fresh server state per run avoids cross-run
# bleed (the pgdata mount in the Letta docs is for local dev persistence).
# The host-gateway alias lets the containerized Letta call back into the
# adapter's self-hosted MCP server on the runner via host.docker.internal.
set -euo pipefail

# Pinned to the digest the lane was validated against (letta 0.16.8): the
# image is mutable upstream and this script patches one of its internal files,
# so an unpinned pull could silently break the patch target or server
# behavior. Bump deliberately: update the pin, re-run the lane, commit both.
LETTA_IMAGE="letta/letta:0.16.8@sha256:aa66c3eeee13d2dfc40c650d709b550237ee31bfc91942a52fa488a13fa8c102"

# ANTHROPIC_API_KEY is optional: the lane's default LETTA_MODEL is an OpenAI
# handle, but forwarding the key lets LETTA_MODEL select an anthropic/* one.
# The port binds loopback only — the tests run on the same host, and the test
# server is unauthenticated.
docker run -d --name letta-server \
  -p 127.0.0.1:8283:8283 \
  --add-host=host.docker.internal:host-gateway \
  -e OPENAI_API_KEY="${OPENAI_API_KEY:?OPENAI_API_KEY is required for the Letta server}" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  "$LETTA_IMAGE"

wait_healthy() {
  # GET /v1/health/ returns {status, version} once the server is up. Fail
  # loudly if it never comes up — otherwise the step would go green with a
  # dead server and the lane would fail opaquely at test time.
  local ready=false
  for _ in $(seq 1 45); do
    if curl -fsS http://localhost:8283/v1/health/; then ready=true; break; fi
    sleep 2
  done
  if [ "$ready" != true ]; then
    echo "Letta server did not become healthy on :8283" >&2
    docker logs letta-server 2>&1 | tail -50 || true
    exit 1
  fi
}

# Relax Letta's MCP URL guard inside the throwaway container: it rejects any
# MCP hostname resolving to a non-public IP (letta/helpers/url_validation.py),
# and the adapter's self-hosted MCP server is on the docker host — a private
# IP by definition (host.docker.internal -> the host-gateway address). There
# is no official knob to disable it, so patch the validator and restart. This
# is a single-tenant test server on an isolated runner; the guard protects
# multi-tenant deployments, which this is not.
#
# Patch before the (slow) first boot finishes — only the container filesystem
# is needed, so retry exec briefly, then restart so the server imports the
# patched module. One full health-wait total instead of two.
patched=false
for _ in $(seq 1 15); do
  if docker exec -i letta-server python3 - <<'PY'
from pathlib import Path

path = Path("/app/letta/helpers/url_validation.py")
source = path.read_text()
marker = "# band-e2e: MCP URL guard relaxed"
if marker not in source:
    source += f"""

{marker} — this test server must reach an MCP server on the
# docker host (a private IP by definition), which the guard forbids.
def _band_e2e_allow_private(url, *args, **kwargs):
    if not url:
        raise ValueError("server_url cannot be empty")
    return url

validate_mcp_server_url = _band_e2e_allow_private
"""
    path.write_text(source)
print("url_validation patched")
PY
  then patched=true; break; fi
  sleep 2
done
if [ "$patched" != true ]; then
  echo "Failed to patch Letta's MCP URL guard" >&2
  docker logs letta-server 2>&1 | tail -50 || true
  exit 1
fi
# Verify the patch behaviorally, not textually: a fresh interpreter must
# accept a private-IP MCP URL through the patched validator. A silently
# ineffective patch would otherwise surface much later as "discovered no
# tools" cell failures.
if ! docker exec -i letta-server python3 - <<'PY'
from letta.helpers.url_validation import validate_mcp_server_url

validate_mcp_server_url("http://host.docker.internal:1234/sse")
print("url_validation patch verified (private-IP URL accepted)")
PY
then
  echo "Letta MCP URL guard patch is not effective" >&2
  exit 1
fi

docker restart letta-server
wait_healthy

echo "LETTA_BASE_URL=http://localhost:8283" >> "$GITHUB_ENV"
