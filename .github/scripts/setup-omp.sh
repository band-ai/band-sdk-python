#!/usr/bin/env bash
# Install Bun and pin OMP (@oh-my-pi/pi-coding-agent) for the `backends` e2e lane.
set -euo pipefail

OMP_PACKAGE="${OMP_PACKAGE:-@oh-my-pi/pi-coding-agent@18.2.8}"
OMP_MIN_BUN="${OMP_MIN_BUN:-1.3.14}"

if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash
  export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
  export PATH="$BUN_INSTALL/bin:$PATH"
  bun_bin="$BUN_INSTALL/bin"
  if command -v cygpath >/dev/null 2>&1; then
    bun_bin="$(cygpath -w "$bun_bin")"
  fi
  if [[ -n "${GITHUB_PATH:-}" ]]; then
    printf '%s\n' "$bun_bin" >> "$GITHUB_PATH"
  fi
fi

bun_version="$(bun --version)"
IFS=. read -r bun_major bun_minor bun_patch <<< "$bun_version"
IFS=. read -r required_major required_minor required_patch <<< "$OMP_MIN_BUN"
if (( bun_major < required_major \
  || (bun_major == required_major && bun_minor < required_minor) \
  || (bun_major == required_major && bun_minor == required_minor && bun_patch < required_patch) )); then
  echo "Bun >= $OMP_MIN_BUN is required; found $bun_version" >&2
  exit 1
fi

bun --version
bun install -g "${OMP_PACKAGE}"
omp --version
omp acp --help >/dev/null

echo "OMP ready (package=${OMP_PACKAGE}, min Bun=${OMP_MIN_BUN})"
