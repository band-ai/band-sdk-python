#!/usr/bin/env bash
set -euo pipefail

resolve_wheel_path() {
  local value="$1"
  if [[ -z "${value}" ]]; then
    echo ""
    return 0
  fi

  if [[ -d "${value}" ]]; then
    if [[ ! -r "${value}" ]]; then
      echo "[claude-sdk-entrypoint] Wheel directory is not readable: ${value}" >&2
      exit 2
    fi
    local wheel
    wheel="$(find "${value}" -maxdepth 1 -type f -name '*.whl' 2>/dev/null | sort | head -n1)"
    if [[ -n "${wheel}" ]]; then
      echo "${wheel}"
      return 0
    fi

    echo "[claude-sdk-entrypoint] Wheel directory provided but no .whl files found: ${value}" >&2
    exit 2
  fi

  echo "${value}"
}

install_wheel() {
  local env_name="$1" wheel_path="$2" label="$3"
  # Treat whitespace-only values as unset
  [[ -z "${wheel_path// /}" ]] && return 0
  local resolved
  resolved="$(resolve_wheel_path "$wheel_path")"
  if [[ -f "$resolved" ]]; then
    echo "[claude-sdk-entrypoint] Installing local $label wheel: $resolved"
    uv pip install --python /app/.venv/bin/python --force-reinstall "$resolved"
  else
    echo "[claude-sdk-entrypoint] $env_name is set but file does not exist: $resolved" >&2
    exit 2
  fi
}

install_wheel "PHOENIX_CHANNELS_CLIENT_WHEEL" "${PHOENIX_CHANNELS_CLIENT_WHEEL:-}" "phoenix client"

# Validate required workspace mount
if [[ ! -d "/workspace/repo" ]]; then
  echo "[claude-sdk-entrypoint] ERROR: /workspace/repo not mounted. Check docker-compose volumes." >&2
  exit 1
fi

# Configure git safe.directory for bind-mounted repos.
# If the host .gitconfig is mounted read-only, write to a separate file
# and include the host config from it.
if ! git config --global --add safe.directory /workspace/repo 2>/dev/null; then
  export GIT_CONFIG_GLOBAL="${HOME}/.gitconfig-local"
  # Include the host .gitconfig if it exists
  if [[ -f "${HOME}/.gitconfig" ]]; then
    git config --global --add include.path "${HOME}/.gitconfig"
  fi
  for dir in /workspace/repo ${GIT_SAFE_DIRS:+${GIT_SAFE_DIRS//,/ }}; do
    [ -d "$dir" ] && git config --global --add safe.directory "$dir"
  done
else
  # Host .gitconfig is writable, add remaining dirs
  for dir in ${GIT_SAFE_DIRS:+${GIT_SAFE_DIRS//,/ }}; do
    [ -d "$dir" ] && git config --global --add safe.directory "$dir"
  done
fi

exec "$@"
