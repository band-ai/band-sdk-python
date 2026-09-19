#!/usr/bin/env bash
# Install the pinned OMP CLI for the backends E2E lane.
set -euo pipefail

OMP_VERSION="${OMP_VERSION:-18.2.6}"
npm install -g "@oh-my-pi/pi-coding-agent@${OMP_VERSION}"
omp --version
