#!/usr/bin/env bash
# Install + start the OpenCode server for the `backends` e2e lane.
#
# Reads OPENCODE_ZEN_API_KEY (job env, the Zen provider key) and
# OPENCODE_SERVE_LOG (job env, the checkout-relative server log path), and exports
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
  "small_model": "opencode/mimo-v2.6-flash-free",
  "provider": {
    "opencode": { "options": { "apiKey": "{env:OPENCODE_ZEN_API_KEY}" } }
  },
  "permission": {
    "bash": "ask"
  }
}
JSON

if [ "${BAND_E2E_FIRST_ATTEMPT_DIAGNOSTICS:-false}" = "true" ]; then
  # The released native plugin wraps the existing provider fetch, preserving its
  # endpoint, request, abort signal and streaming bytes.
  OPENCODE_CONFIG_JSON=$(node --input-type=module - "$PWD" "$OPENCODE_CONFIG_JSON" <<'JS'
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const [root, raw] = process.argv.slice(2);
const config = JSON.parse(raw);
config.plugin = [[pathToFileURL(resolve(root, '.github/scripts/opencode-trace.mjs')).href,
  {path: resolve(root, 'artifacts/opencode-stream.jsonl')}]];
process.stdout.write(JSON.stringify(config));
JS
)
  # Each SDK room uses a different native directory; process-level config makes
  # the diagnostic plugin visible in all of them, including on Windows.
  export OPENCODE_CONFIG_CONTENT="$OPENCODE_CONFIG_JSON"
fi

# Serve from an empty throwaway dir: opencode is a coding agent with shell/read/grep
# tools, so in the repo checkout a weak free model wanders into the source instead of
# replying. An empty cwd keeps it on task.
workdir="$(mktemp -d)"
# The server's own loop, permission and session records explain a turn the
# adapter logs can't; the e2e workflow uploads this file.
serve_log="$PWD/${OPENCODE_SERVE_LOG:?OPENCODE_SERVE_LOG is required}"
mkdir -p "$(dirname "$serve_log")"
mkdir -p ~/.config/opencode
printf '%s\n' "$OPENCODE_CONFIG_JSON" > ~/.config/opencode/opencode.json
# Also drop a project-local config in the serve cwd: the native opencode on the
# Windows runner reads its config from %APPDATA%, not ~/.config, but honours a
# cwd-local opencode.json on every platform — so this is the portable placement.
printf '%s\n' "$OPENCODE_CONFIG_JSON" > "$workdir/opencode.json"
( cd "$workdir" && nohup opencode serve --hostname 127.0.0.1 --port 4096 \
    --print-logs --log-level INFO >"$serve_log" 2>&1 & )
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
  tail -50 "$serve_log" 2>/dev/null || true
  exit 1
fi
{
  echo "OPENCODE_BASE_URL=http://127.0.0.1:4096"
  # The `"bash": "ask"` rule above is what makes the server raise a real
  # permission.asked; the manual-approval smoke requires that declared (Dep.
  # OPENCODE_BASH_ASKS), so it fails naming the reason against any other serve.
  echo "E2E_OPENCODE_BASH_ASKS=true"
} >> "$GITHUB_ENV"
