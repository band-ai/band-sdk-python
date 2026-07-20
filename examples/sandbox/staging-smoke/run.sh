#!/usr/bin/env bash
# Provisions the Band agent/room fresh (via `probe.py --label provision` —
# never a static credential), then launches the deterministic agent inside
# the sandbox.
#
# Keep this terminal open: it stays in the foreground so its log shows the
# WebSocket connect/disconnect/reconnect activity the smoke is proving.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

: "${SBX_SANDBOX:?Set SBX_SANDBOX to the sandbox created by setup.sh}"
: "${SBX_WORKSPACE:?Set SBX_WORKSPACE to the same disposable workspace setup.sh used}"
# No BAND_* env vars required here: probe.py reads them from the repo root's
# .env.test (see README), validates them against the production guard, and
# hands the validated values back on stdout below — so the sandboxed agent
# can only ever target the endpoints the guard checked, not whatever the raw
# shell environment happens to hold.

mkdir -p .sandbox-smoke
chmod 700 .sandbox-smoke

SBX_VERSION="$(sbx version | head -1)"
# Query the exact environment agent.py runs in — the PEP 723 script env that
# setup.sh warmed with `uv sync --script agent.py` (override included) — via
# `uv python find --script`, which resolves without installing anything. Not
# a separate `uv run --with` env, which could resolve a different band-sdk
# than the one actually executed.
SDK_VERSION="$(sbx exec --workdir "$SBX_WORKSPACE" "$SBX_SANDBOX" \
  sh -c '"$(uv python find --script agent.py)" -c "import band; print(band.__version__)"' \
  2>/dev/null || echo unknown)"

echo "Provisioning a fresh Band agent + room for this run..."
# The fresh agent key is a secret, so probe.py writes it to this private 0600
# file rather than to stdout (a stream that could be captured into a log); the
# three non-secret KEY=value lines still come back on stdout. The file lives in
# the 700 .sandbox-smoke/ dir and is shredded on exit.
KEY_FILE="$(mktemp "$(pwd)/.sandbox-smoke/agent-key.XXXXXX")"
chmod 600 "$KEY_FILE"
trap 'rm -f "$KEY_FILE"' EXIT

PROVISION_OUTPUT="$(uv run probe.py --label provision \
  --sandbox-name "$SBX_SANDBOX" --sbx-version "$SBX_VERSION" \
  --sdk-version "$SDK_VERSION" --api-key-file "$KEY_FILE")"

if [[ "$(echo "$PROVISION_OUTPUT" | wc -l | tr -d ' ')" != "3" ]]; then
  echo "Provisioning stdout was not exactly the expected three KEY=value lines; aborting." >&2
  exit 1
fi

BAND_AGENT_ID="$(echo "$PROVISION_OUTPUT" | sed -n 's/^BAND_AGENT_ID=//p')"
BAND_WS_URL="$(echo "$PROVISION_OUTPUT" | sed -n 's/^BAND_WS_URL=//p')"
BAND_REST_URL="$(echo "$PROVISION_OUTPUT" | sed -n 's/^BAND_REST_URL=//p')"
BAND_API_KEY="$(cat "$KEY_FILE")"

if [[ -z "$BAND_AGENT_ID" || -z "$BAND_API_KEY" || -z "$BAND_WS_URL" || -z "$BAND_REST_URL" ]]; then
  echo "Provisioning did not return the agent credentials and endpoints; aborting." >&2
  exit 1
fi

echo "Agent provisioned: $BAND_AGENT_ID"
echo "Starting the sandboxed agent (sbx exec)..."
echo "Log: .sandbox-smoke/agent.log"

# The freshly-minted credentials are passed into the sandbox via `sbx exec`'s
# own `-e` flags (verified against `sbx exec --help` — it mirrors
# `docker exec`). The key is visible in the host's own process listing for as
# long as `sbx exec` runs, and this shell-sandbox smoke does hand the real key
# to the VM — acceptable for an operator-controlled local smoke. The
# never-in-VM proxy injection (the key stays host-side, the VM sees only the
# `proxy-managed` sentinel) is proven by the kit-based E2E, not by this
# generic shell-sandbox smoke.
sbx exec \
  -e "BAND_AGENT_ID=$BAND_AGENT_ID" \
  -e "BAND_API_KEY=$BAND_API_KEY" \
  -e "BAND_WS_URL=$BAND_WS_URL" \
  -e "BAND_REST_URL=$BAND_REST_URL" \
  --workdir "$SBX_WORKSPACE" \
  "$SBX_SANDBOX" uv run agent.py \
  2>&1 | tee -a .sandbox-smoke/agent.log
