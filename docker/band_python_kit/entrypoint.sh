#!/usr/bin/env bash
set -euo pipefail

# Deliberately thin: reading workspace config, spec.yaml-driven customer venv
# sync, and agent launch belong to the sandbox launcher, not this image. This
# stub only wires CA trust and validates the SDK venv before dropping to the
# non-root runtime user.

# The proxy CA is session-generated per sandbox, so it can only be installed
# at container start, not baked into the image. Runs while still root.
if [[ -n "${PROXY_CA_CERT_B64:-}" ]]; then
  ca_path=/usr/local/share/ca-certificates/sandbox-proxy-ca.crt
  base64 -d <<<"${PROXY_CA_CERT_B64}" > "${ca_path}"
  # A base64 payload that decodes cleanly but isn't a PEM certificate would be
  # silently skipped by update-ca-certificates, leaving trust unwired and
  # every TLS call failing with an opaque error deep in the sandbox. Validate
  # up front and fail loud instead. (Invalid base64 already fails under -e.)
  if ! openssl x509 -noout -in "${ca_path}" >/dev/null 2>&1; then
    echo "[band-python-kit] ERROR: PROXY_CA_CERT_B64 did not decode to a valid PEM certificate" >&2
    exit 1
  fi
  update-ca-certificates >/dev/null
fi

if [[ ! -x "${BAND_SDK_PYTHON:-}" ]]; then
  echo "[band-python-kit] ERROR: SDK interpreter not found at ${BAND_SDK_PYTHON:-<unset>}" >&2
  exit 1
fi

exec setpriv --reuid=agent --regid=agent --init-groups -- "$@"
