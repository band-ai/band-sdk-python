#!/usr/bin/env bash
# Install + start the OpenCode server for the `backends` e2e lane.
#
# Reads OPENCODE_ZEN_API_KEY (job env, the Zen provider key) and exports
# OPENCODE_BASE_URL of the running server to later steps via $GITHUB_ENV.
set -euo pipefail

npm install -g opencode-ai

# The unsecured localhost server reads the Zen key via the {env:...} substitution.
# Free-tier account, so the config pins free models (incl. the small/title model)
# to avoid paid calls.
read -r -d '' OPENCODE_CONFIG_JSON <<'JSON' || true
{
  "$schema": "https://opencode.ai/config.json",
  "small_model": "opencode/mimo-v2.5-free",
  "provider": {
    "opencode": { "options": { "apiKey": "{env:OPENCODE_ZEN_API_KEY}" } }
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
  if curl -fsS http://127.0.0.1:4096/global/health; then ready=true; break; fi
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
echo "OPENCODE_BASE_URL=http://127.0.0.1:4096" >> "$GITHUB_ENV"
