#!/usr/bin/env bash
set -euo pipefail

# Deliberately thin: reading workspace config, spec.yaml-driven customer venv
# sync, and agent launch belong to the sandbox launcher, not this image. This
# stub only wires CA trust and validates the SDK venv before dropping to the
# non-root runtime user.

# The proxy CA is session-generated per sandbox, so it can only be trusted at
# container start, not baked into the image. Runs while still root.
#
# It goes into the kit's own bundle (BAND_CA_BUNDLE, which SSL_CERT_FILE and
# REQUESTS_CA_BUNDLE point at), rebuilt on every start from the read-only
# build-time snapshot of the system roots — never into /etc/ssl/certs. sbx
# installs the same CA into the system store and regenerates that bundle on
# each start itself; regenerating it here as well races sbx and truncates the
# shared bundle to a random subset of its roots on restart.
#
# Rebuilt even without a proxy CA, so a start outside a sandbox never keeps
# a previous session's CA.
proxy_ca=$(mktemp)
staged=$(mktemp "${BAND_CA_BUNDLE}.XXXXXX")
# A failed start must not leave a staged bundle behind to pile up across
# restarts. After the rename below, the staged path no longer exists.
trap 'rm -f "${proxy_ca}" "${staged}"' EXIT
if [[ -n "${PROXY_CA_CERT_B64:-}" ]]; then
  base64 -d <<<"${PROXY_CA_CERT_B64}" > "${proxy_ca}"
  # A base64 payload that decodes cleanly but isn't a PEM certificate would
  # leave trust unwired and every TLS call failing with an opaque error deep
  # in the sandbox. Validate up front and fail loud instead. (Invalid base64
  # already fails under -e.)
  if ! openssl x509 -noout -in "${proxy_ca}" >/dev/null 2>&1; then
    echo "[band-python-kit] ERROR: PROXY_CA_CERT_B64 did not decode to a valid PEM certificate" >&2
    exit 1
  fi
fi
# Staged next to the target and renamed into place, so a reader never sees
# a partially written bundle.
cat "${BAND_CA_ROOTS}" "${proxy_ca}" > "${staged}"
chmod 644 "${staged}"
mv -f "${staged}" "${BAND_CA_BUNDLE}"
rm -f "${proxy_ca}"
trap - EXIT

# Every TLS client the launcher and agent run reads this one bundle: Python's
# ssl, httpx (otherwise only its vendored certifi) and uv via SSL_CERT_FILE,
# requests via REQUESTS_CA_BUNDLE, and git's libcurl, which ignores
# SSL_CERT_FILE, via GIT_SSL_CAINFO. Exported here rather than baked as image
# ENV because sbx injects its own SSL_CERT_FILE/REQUESTS_CA_BUNDLE (the system
# bundle) into the process it starts, overriding the image's.
export SSL_CERT_FILE="${BAND_CA_BUNDLE}" \
  REQUESTS_CA_BUNDLE="${BAND_CA_BUNDLE}" \
  GIT_SSL_CAINFO="${BAND_CA_BUNDLE}"

if [[ ! -x "${BAND_SDK_PYTHON:-}" ]]; then
  echo "[band-python-kit] ERROR: SDK interpreter not found at ${BAND_SDK_PYTHON:-<unset>}" >&2
  exit 1
fi

exec setpriv --reuid=agent --regid=agent --init-groups -- "$@"
