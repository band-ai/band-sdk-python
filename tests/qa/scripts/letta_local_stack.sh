#!/usr/bin/env bash
#
# letta_local_stack.sh — bring up a fully local Letta stack for QA testing.
#
# The letta example needs two services the other adapters don't:
#   1. A Letta server  — runs the Letta agent runtime.
#   2. A band-mcp server — exposes Band platform tools to Letta over MCP.
#
# Using Letta Cloud requires a LETTA_API_KEY and a *publicly reachable* band-mcp.
# This script runs both locally instead, so letta can be tested consistently
# with no external accounts:
#
#   * Letta server   -> Docker container `band-qa-letta` on localhost:8283
#   * band-mcp        -> host process (from the band-mcp source repo) on :8002
#   * QA letta agent  -> spawned by run.py on the host, talks to both
#
# Network path: the Letta *server* (in Docker) calls band-mcp on the host via
# `host.docker.internal:8002`. Stock Letta images ship an SSRF guard
# (`validate_mcp_server_url`) that rejects private/loopback targets, which makes
# any local self-hosted MCP server unreachable. This script applies a
# documented, TEST-ONLY relaxation to the *disposable* QA container so local MCP
# works. It is never applied to anything but this throwaway container.
#
# Usage:
#   tests/qa/scripts/letta_local_stack.sh up       # start + wire everything
#   tests/qa/scripts/letta_local_stack.sh down     # stop + remove
#   tests/qa/scripts/letta_local_stack.sh status    # show state
#
# Env overrides:
#   BAND_MCP_DIR   band-mcp source repo (default: ~/band/thenvoi-mcp)
#   LETTA_PORT     Letta server port    (default: 8283)
#   BAND_MCP_PORT  band-mcp port        (default: 8002)
#   LETTA_MODEL    LLM model for Letta  (default: openai/gpt-4o-mini)
#
set -euo pipefail

QA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$QA_DIR/../.." && pwd)"

BAND_MCP_DIR="${BAND_MCP_DIR:-$HOME/band/thenvoi-mcp}"
BAND_MCP_BRANCH="${BAND_MCP_BRANCH:-rename-band-mcp}"
LETTA_PORT="${LETTA_PORT:-8283}"
BAND_MCP_PORT="${BAND_MCP_PORT:-8002}"
LETTA_MODEL="${LETTA_MODEL:-openai/gpt-4o-mini}"
LETTA_CONTAINER="band-qa-letta"
MCP_PID_FILE="/tmp/band-qa-band-mcp.pid"
MCP_LOG_FILE="/tmp/band-qa-band-mcp.log"

LETTA_AGENT_CONFIG="$REPO_ROOT/examples/letta/agent_config.yaml"
LETTA_ENV_FILE="$REPO_ROOT/examples/letta/.env"
QA_ENV_FILE="$QA_DIR/.env"

# Prefer the project venv python (has PyYAML); fall back to system python3.
PYTHON="$REPO_ROOT/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

log() { printf '\033[1;36m[letta-stack]\033[0m %s\n' "$*"; }
err() { printf '\033[1;31m[letta-stack] ERROR:\033[0m %s\n' "$*" >&2; }

read_env() {  # read_env VAR FILE -> value of VAR= in FILE
  grep -E "^$1=" "$2" 2>/dev/null | head -1 | cut -d= -f2- || true
}

read_yaml_key() {  # read letta agent api_key from agent_config.yaml
  "$PYTHON" -c "import yaml;print((yaml.safe_load(open('$1')) or {}).get('letta_agent',{}).get('api_key',''))" 2>/dev/null || true
}

http_code() {  # http_code URL -> status code, or 000 if unreachable (SSE-safe)
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null)"
  [ -n "$code" ] && echo "$code" || echo "000"
}

apply_ssrf_relaxation() {
  # TEST-ONLY, user-authorized: allow the disposable QA Letta container to
  # connect to a private/loopback MCP target (host.docker.internal). Idempotent.
  docker exec -i "$LETTA_CONTAINER" python3 - <<'PY'
p = "/app/letta/helpers/url_validation.py"
try:
    src = open(p).read()
except FileNotFoundError:
    print("url_validation.py not found — Letta image layout changed; skipping")
    raise SystemExit(0)
marker = '    """Validate MCP HTTP(S) URLs and reject internal/private targets."""'
if "LOCAL QA OVERRIDE" in src:
    print("relaxation already present")
elif marker in src:
    inject = (marker +
        "\n    # LOCAL QA OVERRIDE (test harness, user-authorized): allow loopback/private"
        "\n    # MCP targets so a self-hosted band-mcp on host.docker.internal is reachable."
        "\n    if url:\n        return url")
    open(p, "w").write(src.replace(marker, inject, 1))
    print("relaxation applied")
else:
    print("WARNING: validation marker not found; Letta image may have changed")
PY
}

up() {
  command -v docker >/dev/null || { err "docker not found"; exit 1; }
  [ -f "$QA_ENV_FILE" ] || { err "missing $QA_ENV_FILE (run setup_agents.py first)"; exit 1; }
  [ -f "$LETTA_AGENT_CONFIG" ] || { err "missing $LETTA_AGENT_CONFIG (run setup_agents.py)"; exit 1; }

  local openai_key band_url band_ws agent_key
  openai_key="$(read_env OPENAI_API_KEY "$QA_ENV_FILE")"
  band_url="$(read_env BAND_REST_URL "$QA_ENV_FILE")"; band_url="${band_url:-https://app.band.ai}"
  agent_key="$(read_yaml_key "$LETTA_AGENT_CONFIG")"
  [ -n "$agent_key" ] || { err "no letta_agent api_key in $LETTA_AGENT_CONFIG"; exit 1; }
  [ -n "$openai_key" ] || err "OPENAI_API_KEY not set in $QA_ENV_FILE (Letta needs it)"

  # 1. band-mcp source repo on the band branch -------------------------------
  if [ ! -d "$BAND_MCP_DIR/.git" ]; then
    err "band-mcp source not found at $BAND_MCP_DIR."
    err "Clone it and set BAND_MCP_DIR:  git clone https://github.com/thenvoi/thenvoi-mcp $BAND_MCP_DIR"
    exit 1
  fi
  log "Ensuring band-mcp on '$BAND_MCP_BRANCH' + synced"
  ( cd "$BAND_MCP_DIR" && git fetch -q origin && git checkout -q "$BAND_MCP_BRANCH" && git pull -q --ff-only && uv sync -q )

  # 2. Letta server (Docker) -------------------------------------------------
  if docker ps --filter "name=^${LETTA_CONTAINER}$" --format '{{.Names}}' | grep -q .; then
    log "Letta server already running ($LETTA_CONTAINER)"
  else
    docker rm -f "$LETTA_CONTAINER" >/dev/null 2>&1 || true
    log "Starting Letta server on :$LETTA_PORT"
    docker run -d --name "$LETTA_CONTAINER" -p "${LETTA_PORT}:8283" \
      -e OPENAI_API_KEY="$openai_key" letta/letta:latest >/dev/null
    sleep 6
  fi

  # 3. SSRF relaxation (test-only) + restart so the module reloads -----------
  log "Applying test-only MCP URL relaxation to $LETTA_CONTAINER"
  local relax_out
  relax_out="$(apply_ssrf_relaxation)"
  echo "  $relax_out"
  if printf '%s' "$relax_out" | grep -q "applied"; then
    log "Restarting Letta so the relaxation takes effect"
    docker restart "$LETTA_CONTAINER" >/dev/null
    for _ in $(seq 1 60); do
      [ "$(http_code "http://localhost:${LETTA_PORT}/v1/health/")" = "200" ] && break
      sleep 1
    done
  fi

  # 4. band-mcp host process -------------------------------------------------
  if [ "$(http_code "http://localhost:${BAND_MCP_PORT}/sse")" = "200" ]; then
    log "band-mcp already serving on :$BAND_MCP_PORT"
  else
    log "Starting band-mcp on :$BAND_MCP_PORT (agent scope)"
    BAND_API_KEY="$agent_key" BAND_BASE_URL="$band_url" \
      TRANSPORT=sse HOST=0.0.0.0 PORT="$BAND_MCP_PORT" \
      ALLOWED_HOSTS='["localhost:*","127.0.0.1:*","host.docker.internal:*","0.0.0.0:*"]' \
      nohup bash -c "cd '$BAND_MCP_DIR' && .venv/bin/band-mcp --transport sse --host 0.0.0.0 --port $BAND_MCP_PORT" \
      > "$MCP_LOG_FILE" 2>&1 &
    echo $! > "$MCP_PID_FILE"
    sleep 5
  fi

  # 5. Wire the harness's letta env_file -------------------------------------
  cat > "$LETTA_ENV_FILE" <<EOF
# Local Letta stack — generated by tests/qa/scripts/letta_local_stack.sh
LETTA_BASE_URL=http://localhost:${LETTA_PORT}
LETTA_MODEL=${LETTA_MODEL}
MCP_SERVER_URL=http://host.docker.internal:${BAND_MCP_PORT}/sse
MCP_SERVER_NAME=band
EOF
  log "Wrote $LETTA_ENV_FILE"

  status
  log "Ready. Run:  python tests/qa/run.py --adapter letta --examples 01 --all"
}

down() {
  log "Stopping band-mcp"
  [ -f "$MCP_PID_FILE" ] && kill "$(cat "$MCP_PID_FILE")" 2>/dev/null || true
  pkill -f "band-mcp --transport sse" 2>/dev/null || true
  rm -f "$MCP_PID_FILE"
  log "Removing Letta container"
  docker rm -f "$LETTA_CONTAINER" >/dev/null 2>&1 || true
  log "Down."
}

status() {
  log "Letta server  http://localhost:${LETTA_PORT}        -> HTTP $(http_code "http://localhost:${LETTA_PORT}/v1/health/")"
  log "band-mcp      http://localhost:${BAND_MCP_PORT}/sse -> HTTP $(http_code "http://localhost:${BAND_MCP_PORT}/sse")"
}

case "${1:-}" in
  up) up ;;
  down) down ;;
  status) status ;;
  *) echo "usage: $0 {up|down|status}"; exit 2 ;;
esac
