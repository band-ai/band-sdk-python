#!/usr/bin/env bash
# Install Cursor CLI for the Cursor ACP baseline backend.
#
# Unlike codex/copilot's required job-level keys, CURSOR_API_KEY is optional --
# Dep.CURSOR_CLI (tests/e2e/baseline/toolkit/deps.py) is designed to skip the
# Cursor cells cleanly when it's unset. Skip the install the same way instead
# of hard-failing, so a missing secret doesn't take the codex/opencode/copilot_acp
# cells in this lane down with it.
set -euo pipefail

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  echo "CURSOR_API_KEY not set -- skipping Cursor CLI install; Dep.CURSOR_CLI will skip the Cursor cells."
  exit 0
fi

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
