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

# Demo settings may override providers and presentation; Band identity/endpoints
# always come from the repository's canonical live-test credential file.
DEMO_ENV_FILE="${DEMO_ENV_FILE:-$HERE/.demo.env}"
if [ -f "$DEMO_ENV_FILE" ]; then set -a; . "$DEMO_ENV_FILE"; set +a; fi
unset BAND_API_KEY_USER BAND_REST_URL BAND_WS_URL BAND_SECRET_HOST
if [ -f "$ROOT/.env.test" ]; then set -a; . "$ROOT/.env.test"; set +a; fi

# Endpoints default to PROD (the real demo target); override for dev/staging.
BAND_REST_URL="${BAND_REST_URL:-https://app.band.ai}"
BAND_WS_URL="${BAND_WS_URL:-wss://app.band.ai/api/v1/socket/websocket}"
BAND_NET_HOST="$(printf '%s' "$BAND_REST_URL" | sed -E 's#^[a-z]+://##; s#/.*##')"
BAND_WS_HOST="$(printf '%s' "$BAND_WS_URL" | sed -E 's#^[a-z]+://##; s#/.*##')"
BAND_SECRET_HOST="${BAND_SECRET_HOST:-$BAND_NET_HOST}"
# Band hosts needing egress + key injection: REST and WS (usually the same host).
BAND_HOSTS=("$BAND_NET_HOST")
[ "$BAND_WS_HOST" != "$BAND_NET_HOST" ] && BAND_HOSTS+=("$BAND_WS_HOST")

# Workload references built from the same descriptor, with different targets.
BASE_IMAGE="band-python-kit:local"       # Architect (crewai: pure-Python via uv.lock)
CLI_IMAGE="band-python-kit-cli:local"    # PM + Dev (adds the claude + codex CLIs)
BAND_PLACEHOLDER=proxy-managed

# role | workspace | sbx name | kit image | LLM provider
ROLES=(
  "pm|agents/pm|band-demo-pm|$HERE/.demo/workloads/cli|anthropic"
  "dev|agents/dev|band-demo-dev|$HERE/.demo/workloads/cli|openai"
  "architect|agents/architect|band-demo-architect|$HERE/.demo/workloads/runtime|openai"
)

# Manifest of resources THIS run owns (so cleanup removes only these).
RUN_DIR="$HERE/.demo/run"
MF_SANDBOXES="$RUN_DIR/sandboxes"     # one sbx name per line
MF_SECRETS="$RUN_DIR/secrets"          # "sandbox<TAB>placeholder" per scoped secret
MF_POLICY="$RUN_DIR/policy"            # global network host per line (rules we added)
MF_TMUX="$RUN_DIR/tmux"                # tmux session holding sbx attachments
TMUX_SESSION="band-demo-$$"
TMUX_STARTED=0

log()  { printf '\n=== %s ===\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
require() { command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not found on PATH" >&2; exit 1; }; }
record() { printf '%s\n' "$2" >>"$1"; }   # append line "$2" to manifest "$1"


# Provider -> "<llm-host> <proxy-env-var> <placeholder>". One source, read by both
# egress and secret setup so a provider's host can't drift between them.
provider_llm() {
  case "$1" in
    anthropic) echo "api.anthropic.com ANTHROPIC_PROXY_KEY sk-ant-band-demo" ;;
    openai)    echo "api.openai.com OPENAI_PROXY_KEY sk-band-demo" ;;
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
  docker buildx build -f "$KIT_DIR/spec.yaml" --target runtime --load -t "$BASE_IMAGE" "$ROOT"
  docker save "$BASE_IMAGE" | sbx template load /dev/stdin
  docker buildx build -f "$KIT_DIR/spec.yaml" --target cli --load -t "$CLI_IMAGE" "$ROOT"
  docker save "$CLI_IMAGE" | sbx template load /dev/stdin
  # Local sbx builds use descriptor-directory context, so wrap the loaded
  # workload while retaining the canonical metadata, policy and instructions.
  uv run --project "$ROOT" python - "$ROOT" "$HERE/.demo/workloads" "$BASE_IMAGE" "$CLI_IMAGE" <<'PYWORKLOAD'
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from tests.docker.toolkit.sbx_cli import write_local_workload
_, destination, runtime, cli = sys.argv[1:]
for stage, image in (("runtime", runtime), ("cli", cli)):
    write_local_workload(image, Path(destination) / stage)
PYWORKLOAD
}

# --- Create --------------------------------------------------------------------

provision() {
  log "Provisioning demo agents"
  ( cd "$HERE" && uv run provision.py )   # persists ids for partial-failure cleanup
  # shellcheck source=/dev/null
  source "$HERE/.demo/agents.env"          # DEMO_PM_ID / _APIKEY / _NAME, etc.
}

# Global egress rule, in force BEFORE sbx create so the agent's startup traffic
# is never blocked (sbx can't scope a rule to a not-yet-created sandbox). Records
# only rules we actually added, and is idempotent against the operator's policy.
allow_global() {
  local host="$1" decision status=0 allowed
  decision="$(sbx policy check network "$host" --json)" || status=$?
  allowed="$(printf '%s' "$decision" | uv run --no-project python -c '
import json, sys
state = json.load(sys.stdin)
allowed = state["allowed"]
status = int(sys.argv[1])
if not isinstance(allowed, bool) or status != (0 if allowed else 1):
    raise ValueError("Invalid network policy decision")
print("true" if allowed else "false")
' "$status")" || { warn "could not inspect network policy for $host"; return 1; }
  [ "$allowed" = true ] && return 0
  record "$MF_POLICY" "$host"
  sbx policy allow network "$host" >/dev/null
}

owned_policy_resources() {
  sbx policy ls --source local --type network --decision allow --json | uv run --no-project python -c '
import json, sys
rules = json.load(sys.stdin)["rules"]
if not isinstance(rules, list):
    raise ValueError("Invalid policy rule listing")
for rule in rules:
    if rule["scope"] == "global":
        resources = rule["resources"]
        if not isinstance(resources, list) or any(not isinstance(host, str) for host in resources):
            raise ValueError("Invalid policy resources")
        for host in resources:
            print(host)
'
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
  local spec name existing
  existing="$(sbx ls -q)" || { warn "could not inspect existing sandboxes"; return 1; }
  for spec in "${ROLES[@]}"; do
    IFS='|' read -r _ _ name _ _ <<<"$spec"
    if printf '%s\n' "$existing" | grep -qxF "$name"; then
      echo "ERROR: sandbox '$name' already exists — run './launch.sh down' first" >&2
      exit 1
    fi
    local secrets
    secrets="$(sbx secret ls --sandbox "$name" -q)"
    if [ -n "$secrets" ]; then
      echo "ERROR: unowned scoped secrets exist for '$name'; resolve them before provisioning" >&2
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

  # Placeholder environment is captured at creation; real values travel on stdin.
  # Coding agents map their non-reserved proxy variable to the provider variable.
  local llm_host llm_env llm_ph
  read -r llm_host llm_env llm_ph <<<"$(provider_llm "$provider")"
  record "$MF_SECRETS" "$(printf '%s\t%s' "$name" "$llm_ph")"
  printf '%s' "$provider_key" | sbx secret set-custom --sandbox "$name" --host "$llm_host" --env "$llm_env" --placeholder "$llm_ph" >/dev/null

  # Band key: host-held sentinel swap, scoped per sandbox. Cover every Band host
  # (REST + WS); an explicit BAND_SECRET_HOST override (e.g. a wildcard) wins.
  local secret_hosts=("${BAND_HOSTS[@]}")
  [ "$BAND_SECRET_HOST" != "$BAND_NET_HOST" ] && secret_hosts=("$BAND_SECRET_HOST")
  local host_args=() h
  for h in "${secret_hosts[@]}"; do host_args+=(--host "$h"); done
  record "$MF_SECRETS" "$(printf '%s\t%s' "$name" "$BAND_PLACEHOLDER")"
  printf '%s' "$agent_key" | sbx secret set-custom --sandbox "$name" "${host_args[@]}" --env BAND_API_KEY --placeholder "$BAND_PLACEHOLDER" >/dev/null

  record "$MF_SANDBOXES" "$name"
  sbx create --name "$name" "$image" "$stage"
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
  local failed=0 name sandbox host session placeholder

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
      local existing
      if ! existing="$(sbx ls -q)"; then
        warn "could not inspect sandbox $name"
        failed=1
        continue
      fi
      printf '%s\n' "$existing" | grep -qxF "$name" || continue
      sbx rm -f "$name" >/dev/null || { warn "could not remove sandbox $name"; failed=1; }
    done <"$MF_SANDBOXES"
  fi

  # Creation or removal may fail before sbx removes the scoped secrets.
  if [ -f "$MF_SECRETS" ]; then
    while IFS=$'\t' read -r sandbox placeholder; do
      [ -n "$sandbox" ] || continue
      local stored
      if ! stored="$(sbx secret ls --sandbox "$sandbox" -q)"; then
        warn "could not inspect secrets for $sandbox"
        failed=1
        continue
      fi
      printf '%s\n' "$stored" | grep -qxF "$placeholder" || continue
      sbx secret rm --placeholder "$placeholder" --sandbox "$sandbox" -f >/dev/null 2>&1 \
        || { warn "leftover secret for $sandbox; remove: sbx secret rm --placeholder $placeholder --sandbox $sandbox -f"; failed=1; }
    done <"$MF_SECRETS"
  fi

  if [ -f "$MF_POLICY" ]; then
    while IFS= read -r host; do
      [ -n "$host" ] || continue
      local resources
      if ! resources="$(owned_policy_resources)"; then
        warn "could not inspect recorded policy for $host"
        failed=1
        continue
      fi
      printf '%s\n' "$resources" | grep -qxF "$host" || continue
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
    echo "cleanup incomplete — manifest kept at $RUN_DIR; rerun './launch.sh down' to retry"
    return 1
  fi
}

# --- Orchestration -------------------------------------------------------------

up() {
  check_tools
  check_keys
  check_attach_tool
  assert_names_free
  [ -f "$HERE/.demo/workloads/runtime/band-workload.yaml" ] && [ -f "$HERE/.demo/workloads/cli/band-workload.yaml" ] || { echo "ERROR: run launch.sh build first" >&2; exit 1; }
  if [ -e "$RUN_DIR" ]; then
    echo "ERROR: previous demo manifest exists — run './launch.sh down' first" >&2
    exit 1
  fi
  ( cd "$HERE" && uv run provision.py check )
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
