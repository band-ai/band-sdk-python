#!/usr/bin/env bash
#
# One-command launcher for the Band Docker demo: three agents (PM / Developer /
# Architect), each in its own sbx sandbox, each a different framework, one Band
# room. Secrets stay on the host and are injected on the wire — never in a VM.
#
# Three phases with explicit resource ownership: every sandbox, scoped secret,
# and global policy rule this run creates is recorded in a manifest under
# .demo/run/, and cleanup removes ONLY what it recorded — so a stray or
# pre-existing band-demo-* is never touched, and failures are reported (not
# silently swallowed).
#
# Subcommands:
#   ./launch.sh build   Build + load the kit images (needs Docker + sbx only).
#   ./launch.sh up       Provision, create sandboxes, run the meeting (default).
#   ./launch.sh down     Remove exactly what the last run recorded.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
KIT_DIR="$ROOT/docker/band_python_kit"

# One-file setup: drop endpoints + keys in an env file, loaded here. Prod is the
# default; target dev by pointing DEMO_ENV_FILE at the repo .env.test.
DEMO_ENV_FILE="${DEMO_ENV_FILE:-$HERE/.demo.env}"
# shellcheck source=/dev/null
if [ -f "$DEMO_ENV_FILE" ]; then set -a; . "$DEMO_ENV_FILE"; set +a; fi

# Endpoints default to PROD (the real demo target); override for dev/staging.
BAND_REST_URL="${BAND_REST_URL:-https://app.band.ai}"
BAND_WS_URL="${BAND_WS_URL:-wss://app.band.ai/api/v1/socket/websocket}"
BAND_NET_HOST="$(printf '%s' "$BAND_REST_URL" | sed -E 's#^[a-z]+://##; s#/.*##')"
BAND_WS_HOST="$(printf '%s' "$BAND_WS_URL" | sed -E 's#^[a-z]+://##; s#/.*##')"
BAND_SECRET_HOST="${BAND_SECRET_HOST:-$BAND_NET_HOST}"
# Band hosts needing egress + key injection: REST and WS (usually the same host).
BAND_HOSTS=("$BAND_NET_HOST")
[ "$BAND_WS_HOST" != "$BAND_NET_HOST" ] && BAND_HOSTS+=("$BAND_WS_HOST")

# Image names must match the kit spec's sandbox.image (band-python-kit:local).
BASE_IMAGE="band-python-kit:local"       # Architect (crewai: pure-Python via uv.lock)
CLI_IMAGE="band-python-kit-cli:local"    # PM + Dev (adds the claude + codex CLIs)

# role | workspace | sbx name | kit image | LLM provider
ROLES=(
  "pm|agents/pm|band-demo-pm|$CLI_IMAGE|anthropic"
  "dev|agents/dev|band-demo-dev|$CLI_IMAGE|openai"
  "architect|agents/architect|band-demo-architect|$BASE_IMAGE|openai"
)

# Manifest of resources THIS run owns (so cleanup removes only these).
RUN_DIR="$HERE/.demo/run"
MF_SANDBOXES="$RUN_DIR/sandboxes"     # one sbx name per line
MF_SECRETS="$RUN_DIR/secrets"          # "sandbox<TAB>host" per line (scoped set-custom)
MF_POLICY="$RUN_DIR/policy"            # global network host per line (rules we added)
MF_TMUX="$RUN_DIR/tmux"                # tmux session holding sbx attachments
TMUX_SESSION="band-demo-$$"
TMUX_STARTED=0

log()  { printf '\n=== %s ===\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
require() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found on PATH" >&2; exit 1; }; }
record() { printf '%s\n' "$2" >>"$1"; }   # append line "$2" to manifest "$1"
sandbox_exists() { sbx ls -q 2>/dev/null | grep -qxF "$1"; }

# set-custom refuses to overwrite an existing env for a host, so clear any leftover
# scoped secret (from a crashed run or prior manual use — secrets survive `sbx rm`)
# before setting, keeping `up` idempotent.
clear_secret() { sbx secret rm --sandbox "$1" --host "$2" -f >/dev/null 2>&1 || true; }

# Provider -> "<llm-host> <proxy-env-var> <placeholder>". One source, read by both
# egress and secret setup so a provider's host can't drift between them.
provider_llm() {
  case "$1" in
    anthropic) echo "api.anthropic.com ANTHROPIC_PROXY_KEY sk-ant-{rand}" ;;
    openai)    echo "api.openai.com OPENAI_PROXY_KEY sk-{rand}" ;;
    *) echo "ERROR: unknown provider '$1'" >&2; return 1 ;;
  esac
}

# --- Preflight (split: image build needs tools only, not live keys) ------------

check_tools() {
  require sbx
  require uv
  require docker
  docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running" >&2; exit 1; }
  # sbx needs an initialized global policy before custom rules / sandbox use.
  sbx policy ls >/dev/null 2>&1 || {
    echo "ERROR: sbx policy not initialized — run: sbx policy init balanced" >&2; exit 1;
  }
  echo "tools ok — sbx: $(sbx version 2>/dev/null | head -1 || echo '?')"
}

check_keys() {
  : "${BAND_API_KEY_USER:?BAND_API_KEY_USER is required (conductor + agent provisioning)}"
  : "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required (PM agent)}"
  : "${OPENAI_API_KEY:?OPENAI_API_KEY is required (Developer + Architect agents)}"
  echo "keys ok — Band: $BAND_REST_URL"
}

check_attach_tool() {
  if command -v tmux >/dev/null 2>&1; then return; fi
  if [ -z "${DEMO_HEADLESS:-}" ] && command -v osascript >/dev/null 2>&1; then return; fi
  echo "ERROR: tmux is required to keep sandbox agents attached in headless or non-macOS runs" >&2
  exit 1
}

# --- Build ---------------------------------------------------------------------

build() {
  log "Building kit images"
  docker build -f "$KIT_DIR/Dockerfile" -t "$BASE_IMAGE" "$ROOT"
  docker save "$BASE_IMAGE" | sbx template load /dev/stdin
  # PM + Dev image: base + the claude and codex CLIs their adapters spawn.
  docker build -f "$HERE/Dockerfile.cli" --build-arg BASE="$BASE_IMAGE" -t "$CLI_IMAGE" "$HERE"
  docker save "$CLI_IMAGE" | sbx template load /dev/stdin
}

# --- Create --------------------------------------------------------------------

provision() {
  log "Provisioning demo agents"
  ( cd "$HERE" && uv run provision.py )   # self-rolls-back on partial failure
  # shellcheck source=/dev/null
  source "$HERE/.demo/agents.env"          # DEMO_PM_ID / _APIKEY / _NAME, etc.
}

# Global egress rule, in force BEFORE sbx create so the agent's startup traffic
# is never blocked (sbx can't scope a rule to a not-yet-created sandbox). Records
# only rules we actually added, and is idempotent against the operator's policy.
allow_global() {
  local host="$1"
  sbx policy check network "$host" >/dev/null 2>&1 && return 0
  sbx policy allow network "$host" >/dev/null
  record "$MF_POLICY" "$host"
}

grant_egress() {
  local h spec provider seen=" "
  for h in "${BAND_HOSTS[@]}"; do allow_global "$h"; done
  # Provider hosts come from the roles actually in play (deduped) — no hardcoded list.
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r _ _ _ _ provider <<<"$spec"
    h="$(provider_llm "$provider" | cut -d' ' -f1)"
    case "$seen" in *" $h "*) ;; *) allow_global "$h"; seen="$seen$h " ;; esac
  done
}

# Abort rather than clobber a band-demo-* that this run did not create.
assert_names_free() {
  local spec name
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r _ _ name _ _ <<<"$spec"
    if sandbox_exists "$name"; then
      echo "ERROR: sandbox '$name' already exists — run './launch.sh down' first" >&2
      exit 1
    fi
  done
}

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
  # Stage a per-run copy and bake the id + endpoints into THAT, never the tracked
  # source — keeps the git tree clean and avoids leaking a live agent id.
  local stage="$RUN_DIR/workspaces/$role"
  rm -rf "$stage"; mkdir -p "$stage"
  cp -R "$HERE/$workspace/." "$stage/"   # -R + /. copies dotfiles too, and is robust if the dir has only hidden files
  sed -i.bak \
    -e "s/^  id: .*/  id: $agent_id/" \
    -e "s#^  restUrl: .*#  restUrl: $BAND_REST_URL#" \
    -e "s#^  wsUrl: .*#  wsUrl: $BAND_WS_URL#" \
    "$stage/band.yaml" && rm -f "$stage/band.yaml.bak"

  # Secrets FIRST — the placeholder env vars are injected at create time, so they
  # must exist before the sandbox is created (set-custom accepts a new name).
  #
  # Host exposure caveat: sbx 0.35.0 `set-custom` takes the real value only via
  # --value (no stdin), so it briefly sits in this process's args — visible to
  # other processes of the same host user. Fine for a presenter's own laptop; run
  # the demo on a host only you can inspect.
  # sbx reserves the real provider env names (ANTHROPIC_API_KEY / OPENAI_API_KEY)
  # for its built-in wire-only injection, which the coding CLIs can't use — they
  # refuse to send without a local key. So inject under a NON-reserved *_PROXY_KEY
  # var; the workspace's main.py copies it into the real var for the CLI.
  local llm_host llm_env llm_ph
  read -r llm_host llm_env llm_ph <<<"$(provider_llm "$provider")"
  clear_secret "$name" "$llm_host"
  sbx secret set-custom --sandbox "$name" --host "$llm_host" --env "$llm_env" --placeholder "$llm_ph" --value "$provider_key"
  record "$MF_SECRETS" "$(printf '%s\t%s' "$name" "$llm_host")"

  # Band key: host-held sentinel swap, scoped per sandbox. Cover every Band host
  # (REST + WS); an explicit BAND_SECRET_HOST override (e.g. a wildcard) wins.
  local secret_hosts=("${BAND_HOSTS[@]}")
  [ "$BAND_SECRET_HOST" != "$BAND_NET_HOST" ] && secret_hosts=("$BAND_SECRET_HOST")
  local host_args=() h
  for h in "${secret_hosts[@]}"; do host_args+=(--host "$h"); clear_secret "$name" "$h"; done
  sbx secret set-custom --sandbox "$name" "${host_args[@]}" --env BAND_API_KEY --placeholder proxy-managed --value "$agent_key"
  for h in "${secret_hosts[@]}"; do record "$MF_SECRETS" "$(printf '%s\t%s' "$name" "$h")"; done

  sbx create --name "$name" --template "$image" "$KIT_DIR" "$stage"
  record "$MF_SANDBOXES" "$name"
  attach_one "$role" "$name"
}

launch_all() {
  grant_egress   # global rules before any create — no startup race
  local spec role workspace name image provider
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r role workspace name image provider <<<"$spec"
    launch_one "$role" "$workspace" "$name" "$image" "$provider"
  done
}

# --- Presentation --------------------------------------------------------------

# sbx create does not start the kit entrypoint; each agent needs a live pty
# attachment. Start it immediately after create, before creating the next VM.
pane_cmd() { echo "sbx run --name $1"; }

attach_one() {
  local role="$1" name="$2"
  local pane="echo '=== [$role] $name ==='; $(pane_cmd "$name")"
  if command -v tmux >/dev/null 2>&1; then
    if [ "$TMUX_STARTED" -eq 0 ]; then
      record "$MF_TMUX" "$TMUX_SESSION"
      tmux new-session -d -s "$TMUX_SESSION" -n agents "$pane"
      TMUX_STARTED=1
    else
      tmux split-window -t "$TMUX_SESSION" "$pane"
      tmux select-layout -t "$TMUX_SESSION" tiled >/dev/null
    fi
    return
  fi
  local cmd="printf '\\033]0;%s sandbox\\007' '$role'; $pane"
  # Passed as argv, never spliced into the script: AppleScript string literals
  # reject the shell's \033 escape and any embedded double quote.
  osascript -e 'on run argv' \
            -e 'tell application "Terminal" to do script (item 1 of argv)' \
            -e 'tell application "Terminal" to activate' \
            -e 'end run' "$cmd" >/dev/null
}

announce_attachments() {
  if [ "$TMUX_STARTED" -eq 1 ]; then
    printf '\nAgent output is live in tmux — run:\n   tmux attach -t %s        (detach with Ctrl-b then d)\n\n' "$TMUX_SESSION"
  fi
}

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

# --- Cleanup (removes only recorded resources; reports every failure) ----------

cleanup() {
  log "Cleanup"
  local failed=0 name sandbox host session

  if [ -f "$MF_TMUX" ]; then
    while IFS= read -r session; do
      [ -n "$session" ] || continue
      if ! command -v tmux >/dev/null 2>&1; then
        warn "tmux is unavailable; close session $session manually"
        failed=1
      elif tmux has-session -t "$session" 2>/dev/null; then
        tmux kill-session -t "$session" 2>/dev/null \
          || { warn "could not close tmux session $session"; failed=1; }
      fi
    done <"$MF_TMUX"
  fi

  if [ -f "$MF_SANDBOXES" ]; then
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      sandbox_exists "$name" || continue   # already gone (e.g. a retried down)
      sbx rm -f "$name" >/dev/null || { warn "could not remove sandbox $name"; failed=1; }
    done <"$MF_SANDBOXES"
  fi

  # Scoped secrets persist in the host store after a sandbox is removed — a failed
  # removal leaves a real key behind, so surface it loudly.
  if [ -f "$MF_SECRETS" ]; then
    while IFS=$'\t' read -r sandbox host; do
      [ -n "$sandbox" ] || continue
      sbx secret rm --sandbox "$sandbox" --host "$host" -f >/dev/null 2>&1 \
        || { warn "leftover secret for $sandbox ($host); remove: sbx secret rm --sandbox $sandbox --host $host -f"; failed=1; }
    done <"$MF_SECRETS"
  fi

  if [ -f "$MF_POLICY" ]; then
    while IFS= read -r host; do
      [ -n "$host" ] || continue
      sbx policy check network "$host" >/dev/null 2>&1 || continue   # already gone
      sbx policy rm network --resource "$host" >/dev/null 2>&1 \
        || { warn "leftover global egress rule for $host; remove: sbx policy rm network --resource $host"; failed=1; }
    done <"$MF_POLICY"
  fi

  # Meeting rooms and provisioned agents: provision.py deletes by the ids recorded.
  ( cd "$HERE" && uv run provision.py delete ) || { warn "room/agent deletion reported an error"; failed=1; }

  rm -f "$HERE/.demo/room.url"
  if [ "$failed" -eq 0 ]; then
    rm -rf "$RUN_DIR"   # drop the manifest only after a fully clean teardown
    echo "cleanup complete"
  else
    echo "cleanup finished with WARNINGS — manifest kept at $RUN_DIR; rerun './launch.sh down' to retry"
  fi
}

# --- Orchestration -------------------------------------------------------------

up() {
  check_tools
  check_keys
  check_attach_tool
  assert_names_free
  if [ -e "$RUN_DIR" ]; then
    echo "ERROR: previous demo manifest exists — run './launch.sh down' first" >&2
    exit 1
  fi
  mkdir -p "$RUN_DIR/workspaces"
  # Arm cleanup BEFORE the first mutation so any partial failure is reaped. INT/TERM
  # (Ctrl-C) convert to a normal exit so the single EXIT trap tears down cleanly —
  # a guaranteed manual stop that never depends on the chat or the conductor.
  trap cleanup EXIT
  trap 'exit 130' INT TERM
  provision
  launch_all
  rm -f "$HERE/.demo/room.url"
  announce_attachments
  if [ -z "${DEMO_HEADLESS:-}" ]; then
    ( open_ui ) &          # background: opens the UI once the room exists
  fi

  # The conductor owns the whole lifecycle, including the ending: after the verdict
  # it hands the room to the presenter (interactive) and only returns once the
  # presenter posts the end phrase or goes quiet — so this call blocks for the
  # entire meeting AND the Q&A that follows, with no TTY read here. Headless runs
  # skip the open floor (DEMO_INTERACTIVE=false) and end on the verdict, so
  # automation never waits on an absent presenter. Cleanup then runs via the trap.
  export DEMO_INTERACTIVE="$([ -z "${DEMO_HEADLESS:-}" ] && echo true || echo false)"
  run_conductor
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  case "${1:-up}" in
    build) check_tools; build ;;
    up)    up ;;
    down)  cleanup ;;
    *) echo "usage: $0 {build|up|down}" >&2; exit 2 ;;
  esac
fi
