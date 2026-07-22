#!/usr/bin/env bash
#
# One-command launcher for the Band Docker demo: three agents (PM / Developer /
# Architect), each in its own sbx sandbox, each a different framework, one Band
# room. Secrets stay on the host and are injected on the wire — they never enter
# a VM. Per-agent setup logs stream to labeled panes so the setup is visible.
#
# Subcommands:
#   ./launch.sh build   Build + load the kit images (once, or after a kit change).
#   ./launch.sh up       Provision agents, create sandboxes, run the meeting (default).
#   ./launch.sh down     Tear everything down (sandboxes + provisioned agents).
#
# Prereqs: sbx (>=0.35.0, `sbx login`), uv, and host-side keys —
#   BAND_API_KEY_USER, ANTHROPIC_API_KEY (PM), OPENAI_API_KEY (Dev + Architect).
#
# Lines marked "CONFIRM AT REHEARSAL" use sbx flags that track the kit README
# (sbx 0.35.0); verify them against the installed sbx before the show.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
KIT_DIR="$ROOT/docker/band_python_kit"

# One-file setup: drop endpoints + keys in an env file and they're loaded here.
# Prod is the default (below); to target dev, point DEMO_ENV_FILE at the repo
# .env.test, or put prod values in .demo.env. Explicit env vars still win.
DEMO_ENV_FILE="${DEMO_ENV_FILE:-$HERE/.demo.env}"
# shellcheck source=/dev/null
if [ -f "$DEMO_ENV_FILE" ]; then set -a; . "$DEMO_ENV_FILE"; set +a; fi

# Endpoints default to PROD (the real demo target); override via the env file
# or BAND_REST_URL/BAND_WS_URL for dev/staging/self-hosted.
BAND_REST_URL="${BAND_REST_URL:-https://app.band.ai}"
BAND_WS_URL="${BAND_WS_URL:-wss://app.band.ai/api/v1/socket/websocket}"
# Bare host (strip scheme/path) used both to grant egress and as the proxy
# rewrite host for the Band key. Override BAND_SECRET_HOST for a wildcard.
BAND_NET_HOST="$(printf '%s' "$BAND_REST_URL" | sed -E 's#^[a-z]+://##; s#/.*##')"
BAND_WS_HOST="$(printf '%s' "$BAND_WS_URL" | sed -E 's#^[a-z]+://##; s#/.*##')"
BAND_SECRET_HOST="${BAND_SECRET_HOST:-$BAND_NET_HOST}"
# Band hosts needing egress + key injection: REST and WS (usually the same host).
BAND_HOSTS=("$BAND_NET_HOST")
[ "$BAND_WS_HOST" != "$BAND_NET_HOST" ] && BAND_HOSTS+=("$BAND_WS_HOST")

# Base image name must match the kit spec's sandbox.image (band-python-kit:local).
BASE_IMAGE="band-python-kit:local"       # Architect (crewai: pure-Python via uv.lock)
CLI_IMAGE="band-python-kit-cli:local"    # PM + Dev (adds the claude + codex CLIs)

# role | workspace | sbx name | kit image | LLM provider (sbx built-in secret)
ROLES=(
  "pm|agents/pm|band-demo-pm|$CLI_IMAGE|anthropic"
  "dev|agents/dev|band-demo-dev|$CLI_IMAGE|openai"
  "architect|agents/architect|band-demo-architect|$BASE_IMAGE|openai"
)

log() { printf '\n=== %s ===\n' "$*"; }

require() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found on PATH" >&2; exit 1; }; }

preflight() {
  log "Preflight"
  require sbx
  require uv
  : "${BAND_API_KEY_USER:?BAND_API_KEY_USER is required (conductor + agent provisioning)}"
  : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required (PM agent)}"
  : "${OPENAI_API_KEY:?OPENAI_API_KEY is required (Developer + Architect agents)}"
  echo "sbx: $(sbx version 2>/dev/null | head -1 || echo '?')   Band: $BAND_REST_URL"
}

build() {
  log "Building kit images"
  # Base kit image (name must match the spec's sandbox.image). The workspace
  # uv.lock carries each framework's Python deps, synced at first boot.
  docker build -f "$KIT_DIR/Dockerfile" -t "$BASE_IMAGE" "$ROOT"
  docker save "$BASE_IMAGE" | sbx template load /dev/stdin

  # PM + Dev image: base + the claude and codex CLIs their adapters spawn.
  docker build -f "$HERE/Dockerfile.cli" --build-arg BASE="$BASE_IMAGE" -t "$CLI_IMAGE" "$HERE"
  docker save "$CLI_IMAGE" | sbx template load /dev/stdin
}

provision() {
  log "Provisioning demo agents"
  ( cd "$HERE" && uv run provision.py )
  # shellcheck source=/dev/null
  source "$HERE/.demo/agents.env"   # DEMO_PM_ID / _APIKEY / _NAME, etc.
}

# For each role: bake the agent id into its band.yaml, create the sandbox, grant
# egress, and inject credentials host-side (LLM built-in + Band custom sentinel).
launch_one() {
  local role="$1" workspace="$2" name="$3" image="$4" provider="$5"
  local up_role; up_role="$(printf '%s' "$role" | tr '[:lower:]' '[:upper:]')"
  local id_var="DEMO_${up_role}_ID" key_var="DEMO_${up_role}_APIKEY"
  local agent_id="${!id_var}" agent_key="${!key_var}" provider_key
  case "$provider" in
    anthropic) provider_key="$ANTHROPIC_API_KEY" ;;
    openai)    provider_key="$OPENAI_API_KEY" ;;
  esac

  log "Sandbox: $name ($role, $provider)"
  # Stage a per-run copy of the workspace and bake the id + endpoints into THAT,
  # never the tracked source — keeps the git tree clean and avoids leaking a live
  # agent id into a committed file. Teardown removes the staged copies.
  local stage="$HERE/.demo/workspaces/$role"
  rm -rf "$stage"; mkdir -p "$stage"
  cp "$HERE/$workspace"/* "$stage"/
  sed -i.bak \
    -e "s/^  id: .*/  id: $agent_id/" \
    -e "s#^  restUrl: .*#  restUrl: $BAND_REST_URL#" \
    -e "s#^  wsUrl: .*#  wsUrl: $BAND_WS_URL#" \
    "$stage/band.yaml" && rm -f "$stage/band.yaml.bak"

  # Secrets FIRST — placeholder env vars are injected at create time, so the
  # secrets must exist before the sandbox is created (set-custom accepts a
  # not-yet-created name).
  #
  # LLM key: sbx reserves the real provider env names (ANTHROPIC_API_KEY /
  # OPENAI_API_KEY) for its built-in *wire-only* injection, which the coding CLIs
  # can't use — they refuse to send without a local key. So inject the placeholder
  # under a NON-reserved *_PROXY_KEY var; the workspace's main.py copies it into
  # the real var for the CLI, and the proxy swaps the placeholder on the wire.
  local llm_host llm_env llm_ph
  case "$provider" in
    anthropic) llm_host="api.anthropic.com"; llm_env="ANTHROPIC_PROXY_KEY"; llm_ph='sk-ant-{rand}' ;;
    openai)    llm_host="api.openai.com";    llm_env="OPENAI_PROXY_KEY";    llm_ph='sk-{rand}' ;;
  esac
  sbx secret set-custom "$name" --host "$llm_host" --env "$llm_env" --placeholder "$llm_ph" --value "$provider_key"

  # Band key: host-held sentinel swap, scoped per sandbox (each agent's key differs).
  # Cover every Band host (REST + WS); an explicit BAND_SECRET_HOST override (e.g. a
  # wildcard) replaces the derived set.
  local secret_hosts=("${BAND_HOSTS[@]}")
  [ "$BAND_SECRET_HOST" != "$BAND_NET_HOST" ] && secret_hosts=("$BAND_SECRET_HOST")
  local host_args=()
  for h in "${secret_hosts[@]}"; do host_args+=(--host "$h"); done
  sbx secret set-custom "$name" "${host_args[@]}" \
    --env BAND_API_KEY --placeholder proxy-managed --value "$agent_key"

  # Create: --kit is the spec dir, --template the loaded image. The agent boots
  # and runs uv sync (its framework deps) — a window of seconds+ before it connects.
  sbx create --name "$name" --kit "$KIT_DIR" --template "$image" band-python-kit "$stage"

  # Policy can only be added after the sandbox exists; do it now, inside the sync
  # window, so the allow rule is in place before the agent's first Band request.
  # (On prod the spec already allows app.band.ai; the LLM host still needs this.)
  for h in "${BAND_HOSTS[@]}"; do sbx policy allow network --sandbox "$name" "$h"; done
  case "$provider" in
    anthropic) sbx policy allow network --sandbox "$name" api.anthropic.com ;;
    openai)    sbx policy allow network --sandbox "$name" api.openai.com ;;
  esac
}

launch_all() {
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r role workspace name image provider <<<"$spec"
    launch_one "$role" "$workspace" "$name" "$image" "$provider"
  done
}

# One Terminal window per sandbox, tailing its in-VM setup log — the visible
# "watch each agent set up" view. macOS Terminal via osascript (no tmux needed).
spawn_terminals() {
  command -v osascript >/dev/null 2>&1 || { echo "osascript not found — skipping per-sandbox windows"; return; }
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r role _ name _ _ <<<"$spec"
    local cmd="printf '\\033]0;%s sandbox\\007' '$role'; echo '=== [$role] $name ==='; sbx exec '$name' tail -n +1 -f /var/log/sbx-kit-startup.log"
    osascript -e "tell application \"Terminal\" to do script \"$cmd\"" >/dev/null 2>&1 || true
  done
}

# Wait for the conductor to create the room (it writes .demo/room.url), then open
# the Band UI in the browser so the presenter can watch + type as the human.
open_ui() {
  local f="$HERE/.demo/room.url"
  for _ in $(seq 1 60); do [ -s "$f" ] && break; sleep 1; done
  [ -s "$f" ] || { echo "room.url not written; open the Band UI manually"; return; }
  echo "Opening Band UI: $(cat "$f")"
  open "$(cat "$f")" >/dev/null 2>&1 || true
}

run_conductor() {
  log "Running the meeting (conductor + circuit breaker)"
  uv run "$HERE/conductor.py"
}

teardown() {
  log "Teardown"
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r _ _ name _ _ <<<"$spec"
    sbx rm -f "$name" 2>/dev/null || true
  done
  ( cd "$HERE" && uv run provision.py delete ) 2>/dev/null || true
  rm -rf "$HERE/.demo/workspaces" "$HERE/.demo/room.url"
}

up() {
  preflight
  provision
  trap teardown EXIT
  launch_all
  rm -f "$HERE/.demo/room.url"
  # DEMO_HEADLESS=1 skips the GUI presentation (used for automated validation).
  if [ -z "${DEMO_HEADLESS:-}" ]; then
    spawn_terminals
    ( open_ui ) &          # background: opens the UI once the room exists
  fi
  run_conductor            # foreground: the meeting narration in this terminal
}

case "${1:-up}" in
  build) preflight; build ;;
  up)    up ;;
  down)  teardown ;;
  *) echo "usage: $0 {build|up|down}" >&2; exit 2 ;;
esac
