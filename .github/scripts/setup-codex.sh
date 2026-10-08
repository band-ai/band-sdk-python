#!/usr/bin/env bash
# Install + authenticate the Codex CLI for the `backends` e2e lane.
#
# Reads OPENAI_API_KEY (job env) for login and exports CODEX_CWD /
# E2E_CODEX_CWD_IS_DISPOSABLE to later steps via $GITHUB_ENV. The model comes
# from band.core.defaultmodels.OPENAI_MODEL via the baseline settings.
set -euo pipefail

# Fail with a clear message if the key is missing: `printenv OPENAI_API_KEY` takes
# the name as an argument (not a shell expansion), so `set -u` wouldn't catch an
# unset key — the login would just fail opaquely with no output.
: "${OPENAI_API_KEY:?OPENAI_API_KEY is required for codex login}"

# Pinned: an unpinned global install lets both CLIs float between runs, so a
# CLI change lands as an unrelated-looking lane failure. Bump deliberately; the
# CLI's bundled catalogue must include band.core.defaultmodels.OPENAI_MODEL.
CODEX_CLI_VERSION="${CODEX_CLI_VERSION:-0.160.0}"
CODEX_ACP_VERSION="${CODEX_ACP_VERSION:-1.6.2}"

npm install -g "@openai/codex@${CODEX_CLI_VERSION}" \
  "@agentclientprotocol/codex-acp@${CODEX_ACP_VERSION}"
printenv OPENAI_API_KEY | codex login --with-api-key
codex login status

# Codex's workspace-write sandbox runs every command through bwrap. The runner
# image ships none, and Codex's bundled fallback needs the unprivileged user
# namespaces Ubuntu 24.04's AppArmor blocks -- so each command fails before it
# runs and the model only reports it couldn't. Setup per Codex's sandboxing docs;
# the probe makes a still-broken sandbox fail here, not as a missing test file.
if [[ "$(uname -s)" == Linux* ]]; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq bubblewrap apparmor-profiles apparmor-utils
  sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
  if ! bwrap --unshare-user --ro-bind / / true; then
    echo "bwrap cannot create a user namespace; Codex's sandbox is unusable" >&2
    exit 1
  fi
fi

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
} >> "$GITHUB_ENV"
