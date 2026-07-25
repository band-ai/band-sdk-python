#!/usr/bin/env bash
# Install + authenticate the Codex CLI for the `backends` e2e lane.
#
# Reads OPENAI_API_KEY (job env) for login and CODEX_MODEL (defaulted here) for
# the model, and exports CODEX_CWD / E2E_CODEX_CWD_IS_DISPOSABLE / CODEX_MODEL to
# later steps via $GITHUB_ENV.
set -euo pipefail

# Fail with a clear message if the key is missing: `printenv OPENAI_API_KEY` takes
# the name as an argument (not a shell expansion), so `set -u` wouldn't catch an
# unset key — the login would just fail opaquely with no output.
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required for codex login}"

# Codex picks a model from its own catalogue (a plain openai default like
# gpt-4o-mini is not one). Keep this current: the OpenAI API hard-deprecates old
# codex models, and a deprecated pin makes every turn stream-error with no tokens
# -- which surfaces as blanket usage/reply assertion failures, not an obvious
# model error. gpt-5-codex is deprecated; gpt-5.3-codex is the current pin.
CODEX_MODEL="${CODEX_MODEL:-gpt-5.3-codex}"

# Pinned for the same reason as the model: an unpinned global install lets both
# CLIs float between runs, so a CLI change lands as an unrelated-looking lane
# failure. Bump deliberately.
CODEX_CLI_VERSION="${CODEX_CLI_VERSION:-0.145.0}"
CODEX_ACP_VERSION="${CODEX_ACP_VERSION:-1.1.7}"

npm install -g "@openai/codex@${CODEX_CLI_VERSION}" \
  "@agentclientprotocol/codex-acp@${CODEX_ACP_VERSION}"
printenv OPENAI_API_KEY | codex login --with-api-key
codex login status

# Codex may write to its working dir, so point it at a throwaway path outside the
# checkout and opt in explicitly (the requirement gate enforces this).
codex_cwd="$(mktemp -d)"
# On the Windows runner this script runs under Git Bash, so `codex` is a native
# .exe that won't understand a /tmp/... msys path — hand it a native path.
# cygpath is absent on Linux, so the value passes through unchanged there.
if command -v cygpath >/dev/null 2>&1; then
  codex_cwd="$(cygpath -w "$codex_cwd")"
fi
{
  echo "CODEX_CWD=${codex_cwd}"
  echo "E2E_CODEX_CWD_IS_DISPOSABLE=true"
  echo "CODEX_MODEL=${CODEX_MODEL}"
} >> "$GITHUB_ENV"
