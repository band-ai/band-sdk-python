#!/usr/bin/env bash
# Install Bun and pin OMP (@oh-my-pi/pi-coding-agent) for the `backends` e2e lane.
set -euo pipefail

OMP_PACKAGE="${OMP_PACKAGE:-@oh-my-pi/pi-coding-agent@18.2.8}"
OMP_MIN_BUN="${OMP_MIN_BUN:-1.3.14}"

if ! command -v bun >/dev/null 2>&1; then
  curl -fsSL https://bun.sh/install | bash
  export BUN_INSTALL="${BUN_INSTALL:-$HOME/.bun}"
  export PATH="$BUN_INSTALL/bin:$PATH"
fi

bun --version
bun install -g "${OMP_PACKAGE}"
omp --version
omp acp --help >/dev/null

echo "OMP ready (package=${OMP_PACKAGE}, min Bun=${OMP_MIN_BUN})"
