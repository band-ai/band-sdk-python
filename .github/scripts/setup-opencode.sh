#!/usr/bin/env bash
# Install + start the OpenCode server for the `backends` e2e lane.
#
# Reads OPENCODE_ZEN_API_KEY (job env, the Zen provider key) and exports
# OPENCODE_BASE_URL / E2E_OPENCODE_BASH_ASKS of the running server to later steps
# via $GITHUB_ENV.
set -euo pipefail

# Pinned: an unpinned global install lets the CLI float between runs, so a server
# behaviour change lands as an unrelated-looking lane failure. Bump deliberately.
OPENCODE_VERSION="${OPENCODE_VERSION:-1.18.4}"

npm install -g "opencode-ai@${OPENCODE_VERSION}"

# The unsecured localhost server reads the Zen key via the {env:...} substitution.
# Free-tier account, so the config pins free models (incl. the small/title model)
# to avoid paid calls.
read -r -d '' OPENCODE_CONFIG_JSON <<'JSON' || true
{
  "$schema": "https://opencode.ai/config.json",
  "small_model": "opencode/mimo-v2.5-free",
  "provider": {
    "opencode": { "options": { "apiKey": "{env:OPENCODE_ZEN_API_KEY}" } }
  },
  "permission": {
    "bash": "ask"
  }
}
JSON

# Serve from an empty throwaway dir: opencode is a coding agent with shell/read/grep
# tools, so in the repo checkout a weak free model wanders into the source instead of
# replying. An empty cwd keeps it on task.
workdir="$(mktemp -d)"
mkdir -p ~/.config/opencode
printf '%s\n' "$OPENCODE_CONFIG_JSON" > ~/.config/opencode/opencode.json
# Also drop a project-local config in the serve cwd: the native opencode on the
# Windows runner reads its config from %APPDATA%, not ~/.config, but honours a
# cwd-local opencode.json on every platform — so this is the portable placement.
printf '%s\n' "$OPENCODE_CONFIG_JSON" > "$workdir/opencode.json"
( cd "$workdir" && nohup opencode serve --hostname 127.0.0.1 --port 4096 \
    >/tmp/opencode-serve.log 2>&1 & )
ready=false
for _ in $(seq 1 30); do
  # --max-time bounds each attempt: without it, a server that accepts the
  # connection but never finishes the response wedges this loop (and the
  # whole job, up to its 240-minute timeout) on a single curl call instead
  # of retrying and failing loudly within the intended ~60s budget.
  if curl -fsS --max-time 5 http://127.0.0.1:4096/global/health; then ready=true; break; fi
  sleep 2
done
# Fail loudly if the server never came up (covers a server that died on launch —
# the backgrounded subshell's exit status doesn't surface that). Otherwise the step
# would go green with a dead server and the lane would fail opaquely at test time.
if [ "$ready" != true ]; then
  echo "OpenCode server did not become healthy on :4096" >&2
  cat /tmp/opencode-serve.log 2>/dev/null | tail -50 || true
  exit 1
fi
{
  echo "OPENCODE_BASE_URL=http://127.0.0.1:4096"
  # The `"bash": "ask"` rule above is what makes the server raise a real
  # permission.asked; the manual-approval smoke requires that declared (Dep.
  # OPENCODE_BASH_ASKS), so it fails naming the reason against any other serve.
  echo "E2E_OPENCODE_BASH_ASKS=true"
} >> "$GITHUB_ENV"
