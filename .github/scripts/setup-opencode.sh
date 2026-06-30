#!/usr/bin/env bash
# Install + start the OpenCode server for the `backends` e2e lane.
#
# Reads OPENCODE_ZEN_API_KEY (job env, the Zen provider key) and exports
# OPENCODE_BASE_URL of the running server to later steps via $GITHUB_ENV.
set -euo pipefail

npm install -g opencode-ai
mkdir -p ~/.config/opencode

# The unsecured localhost server reads the Zen key via the {env:...} substitution.
# Free-tier account, so the config pins free models (incl. the small/title model)
# to avoid paid calls.
cat > ~/.config/opencode/opencode.json <<'JSON'
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
( cd "$workdir" && nohup opencode serve --hostname 127.0.0.1 --port 4096 \
    >/tmp/opencode-serve.log 2>&1 & )
for _ in $(seq 1 30); do
  curl -fsS http://127.0.0.1:4096/global/health && break
  sleep 2
done
echo "OPENCODE_BASE_URL=http://127.0.0.1:4096" >> "$GITHUB_ENV"
