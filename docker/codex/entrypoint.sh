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
      echo "[codex-entrypoint] Wheel directory is not readable: ${value}" >&2
      exit 2
    fi
    local wheel
    wheel="$(find "${value}" -maxdepth 1 -type f -name '*.whl' 2>/dev/null | sort | head -n1)"
    if [[ -n "${wheel}" ]]; then
      echo "${wheel}"
      return 0
    fi

    echo "[codex-entrypoint] Wheel directory provided but no .whl files found: ${value}" >&2
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
    echo "[codex-entrypoint] Installing local $label wheel: $resolved"
    uv pip install --python /app/.venv/bin/python --force-reinstall "$resolved"
  else
    echo "[codex-entrypoint] $env_name is set but file does not exist: $resolved" >&2
    exit 2
  fi
}

install_wheel "PHOENIX_CHANNELS_CLIENT_WHEEL" "${PHOENIX_CHANNELS_CLIENT_WHEEL:-}" "phoenix client"

# Validate required workspace mount
if [[ ! -d "/workspace/repo" ]]; then
  echo "[codex-entrypoint] ERROR: /workspace/repo not mounted. Check docker-compose volumes." >&2
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

# Codex defaults to the WebSocket transport for the OpenAI Responses API
# (wss://api.openai.com/v1/responses). With API-key auth (sk-proj / sk-svcacct)
# that endpoint returns 401 Unauthorized even though REST POST /v1/responses
# succeeds (HTTP 200) with the same key, and Codex's WS->HTTPS fallback drops
# the auth header (openai/codex#15492) so it 401s too. Route Codex through a
# custom provider pinned to the HTTP/REST Responses transport instead
# (verified against a live sk-svcacct key).
#
# Gated on OPENAI_API_KEY because the REST provider authenticates via env_key;
# OAuth/ChatGPT logins (auth.json) use a different transport and are left alone.
# Set CODEX_DISABLE_WEBSOCKET=false to keep Codex's default WebSocket path.
CODEX_REST_PROVIDER_ID="openai_rest"

force_rest_responses_api() {
  local config_file="$1"

  [[ "${CODEX_DISABLE_WEBSOCKET:-true}" == "true" ]] || return 0
  [[ -n "${OPENAI_API_KEY:-}" ]] || return 0
  [[ -f "${config_file}" ]] || return 0

  # Idempotent: skip if the provider definition is already present.
  if grep -q "\[model_providers\.${CODEX_REST_PROVIDER_ID}\]" "${config_file}" 2>/dev/null; then
    return 0
  fi

  # A top-level key must precede any [table] header, so prepend model_provider.
  # Respect an operator-supplied model_provider; only add the provider block then.
  if grep -Eq '^[[:space:]]*model_provider[[:space:]]*=' "${config_file}" 2>/dev/null; then
    echo "[codex-entrypoint] Existing model_provider in config.toml; adding REST provider definition only" >&2
  else
    printf 'model_provider = "%s"\n' "${CODEX_REST_PROVIDER_ID}" \
      | cat - "${config_file}" > "${config_file}.tmp" \
      && mv "${config_file}.tmp" "${config_file}"
  fi

  cat >> "${config_file}" <<EOF

# Forces the HTTP/REST Responses API instead of the WebSocket transport.
[model_providers.${CODEX_REST_PROVIDER_ID}]
name = "OpenAI (REST)"
base_url = "https://api.openai.com/v1"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
supports_websockets = false
EOF
  echo "[codex-entrypoint] Forced REST Responses API via provider '${CODEX_REST_PROVIDER_ID}' (WebSocket disabled)"
}

bootstrap_codex_home() {
  # Build a container-safe runtime CODEX_HOME while preserving core auth/config.
  if [[ "${CODEX_USE_SOURCE_HOME:-false}" == "true" ]]; then
    echo "[codex-entrypoint] Using source CODEX_HOME as requested: ${CODEX_HOME:-${HOME}/.codex}"
    # Leave a mounted (possibly read-only) source config untouched; operators
    # opting into source-home own its provider/transport settings.
    return 0
  fi

  local source_home runtime_home source_config runtime_config
  source_home="${CODEX_HOME:-${HOME}/.codex}"
  runtime_home="${CODEX_RUNTIME_HOME:-/workspace/state/codex-home}"
  source_config="${source_home}/config.toml"
  runtime_config="${runtime_home}/config.toml"

  mkdir -p "${runtime_home}/sessions"

  # Preserve primary auth material when present.
  if [[ -f "${source_home}/auth.json" ]]; then
    cp -f "${source_home}/auth.json" "${runtime_home}/auth.json"
    chmod 600 "${runtime_home}/auth.json" || true
  fi

  if [[ -f "${source_config}" ]]; then
    # Rewrite host absolute ~/.codex references to the mounted source_home path.
    awk -v src="${source_home}" '
      {
        gsub(/\/Users\/[^\/]+\/\.codex/, src);
        gsub(/\/home\/[^\/]+\/\.codex/, src);
        print
      }
    ' "${source_config}" > "${runtime_config}"
  else
    cat > "${runtime_config}" <<EOF
model = "${CODEX_MODEL:-gpt-5.5}"
approval_policy = "never"
sandbox_mode = "danger-full-access"
EOF
  fi

  # Pin Codex to the REST Responses transport to avoid the WebSocket 401.
  force_rest_responses_api "${runtime_config}"

  chmod 600 "${runtime_config}" || true

  export CODEX_HOME="${runtime_home}"
  echo "[codex-entrypoint] Bootstrapped runtime CODEX_HOME=${CODEX_HOME} (source=${source_home})"
}

bootstrap_codex_home

exec "$@"
