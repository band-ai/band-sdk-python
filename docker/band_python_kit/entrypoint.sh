#!/usr/bin/env bash
set -euo pipefail

# Deliberately thin: reading workspace config, spec.yaml-driven customer venv
# sync, and agent launch belong to the sandbox launcher, not this image. This
# stub only wires CA trust and validates the SDK venv before dropping to the
# non-root runtime user.

# The proxy CA is session-generated per sandbox, so it can only be installed
# at container start, not baked into the image. Runs while still root.
if [[ -n "${PROXY_CA_CERT_B64:-}" ]]; then
  base64 -d <<<"${PROXY_CA_CERT_B64}" > /usr/local/share/ca-certificates/sandbox-proxy-ca.crt
  update-ca-certificates >/dev/null
fi

if [[ ! -x "${BAND_SDK_PYTHON:-}" ]]; then
  echo "[band-python-kit] ERROR: SDK interpreter not found at ${BAND_SDK_PYTHON:-<unset>}" >&2
  exit 1
fi

exec setpriv --reuid=agent --regid=agent --init-groups -- "$@"
