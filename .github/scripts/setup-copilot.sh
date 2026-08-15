#!/usr/bin/env bash
# Install the GitHub Copilot CLI for the `backends` e2e lane's copilot_acp adapter.
#
# Auth is Anthropic BYOK: the baseline builder spawns `copilot --acp` with the
# COPILOT_PROVIDER_* env (see tests/e2e/baseline/toolkit/builders.py
# copilot_acp_env), so no GitHub token or login step is involved — BYOK mode
# does not require GitHub authentication (`copilot help providers`). The lane
# gate is the CLI plus ANTHROPIC_API_KEY.
set -euo pipefail

# Fail with a clear message if the key is missing rather than letting a later
# ACP session fail opaquely. printenv takes the name as an argument, so `set -u`
# alone wouldn't catch an unset key.
: "${ANTHROPIC_API_KEY:?ANTHROPIC_API_KEY is required for the Copilot CLI's Anthropic BYOK auth}"

npm install -g @github/copilot
copilot --version
