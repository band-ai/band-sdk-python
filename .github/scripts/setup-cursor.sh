#!/usr/bin/env bash
# Install Cursor CLI for the Cursor ACP baseline backend.
set -euo pipefail

: "${CURSOR_API_KEY:?CURSOR_API_KEY is required for Cursor ACP E2E auth}"

if [[ "${RUNNER_OS:-}" == "Windows" ]]; then
  powershell -NoProfile -Command "irm https://cursor.com/install -UseBasicParsing | iex"
else
  curl --fail --silent --show-error https://cursor.com/install | bash
fi

cursor_bin="${HOME}/.local/bin"
if [[ -d "$cursor_bin" ]]; then
  echo "$cursor_bin" >> "$GITHUB_PATH"
  export PATH="$cursor_bin:$PATH"
fi

agent --version
