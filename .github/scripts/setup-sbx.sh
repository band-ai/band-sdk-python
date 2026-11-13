#!/usr/bin/env bash
# Install, authorize, and sign in the Docker Sandboxes CLI for the `kit` e2e lane.
#
# Reads SBX_DOCKER_USERNAME / SBX_DOCKER_TOKEN (a Docker account and access
# token; `sbx` refuses to create sandboxes until signed in). Docker documents
# Ubuntu 24.04+ with KVM for local sandboxes:
# https://docs.docker.com/ai/sandboxes/install/
set -euo pipefail

: "${SBX_DOCKER_USERNAME:?SBX_DOCKER_USERNAME is required to sign in to sbx}"
: "${SBX_DOCKER_TOKEN:?SBX_DOCKER_TOKEN is required to sign in to sbx}"

# GitHub's Ubuntu runners expose /dev/kvm to root only; the sandbox microVM
# runs as the runner user.
echo 'KERNEL=="kvm", GROUP="kvm", MODE="0666", OPTIONS+="static_node=kvm"' \
  | sudo tee /etc/udev/rules.d/99-kvm4all.rules >/dev/null
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=kvm
test -w /dev/kvm

# The runner already has Docker Engine; add only Docker's apt repo and sbx.
curl -fsSL https://get.docker.com | sudo REPO_ONLY=1 sh
sudo apt-get install -y docker-sbx
sbx version

printf '%s' "$SBX_DOCKER_TOKEN" \
  | sbx login --username "$SBX_DOCKER_USERNAME" --password-stdin
# A fresh host has no global policy, and `sbx run` would prompt for one. The
# kit's own network policy (and the smokes' per-run grants) apply on top.
sbx policy init balanced
