#!/usr/bin/env bash
# Install the GitHub Copilot CLI for the `backends` e2e lane's copilot_acp adapter.
#
# Auth is by environment token: the job env carries a Copilot-entitled GITHUB_TOKEN,
# which the Copilot CLI (and the ACP server it runs via `copilot --acp`) reads
# automatically — so there is no separate login step, unlike codex.
set -euo pipefail

# Fail with a clear message if the token is missing rather than letting a later
# ACP session fail opaquely. printenv takes the name as an argument, so `set -u`
# alone wouldn't catch an unset token.
: "${GITHUB_TOKEN:?GITHUB_TOKEN (from a Copilot-entitled account) is required for the Copilot CLI}"

npm install -g @github/copilot
copilot --version
