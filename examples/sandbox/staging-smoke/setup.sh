#!/usr/bin/env bash
# Preflight + disposable sandbox creation for the Docker Sandbox staging smoke.
#
# Does NOT provision the Band agent/room — that happens in run.sh (via
# `probe.py --label provision`), right before the sandboxed process starts, so
# the freshly-minted credentials never touch disk (see probe.py's docstring).
#
# One-time prerequisites (see README.md): `sbx login`, `sbx policy init balanced`,
# `uv sync --extra dev` (this script and probe.py run via this repo's own dev
# environment, not a standalone PEP 723 script — see probe.py's docstring).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

: "${SBX_SANDBOX:?Set SBX_SANDBOX to the sandbox name to create/use}"
: "${SBX_WORKSPACE:?Set SBX_WORKSPACE to a disposable workspace path under \$HOME (not this checkout)}"

# All other safety checks (sbx installed, staging endpoints set and
# non-production, workspace genuinely outside this checkout) live in
# preflight.py — the one place that logic exists, so setup.sh doesn't carry a
# second, independently-maintained copy of the same checks.
uv run skill/scripts/preflight.py

mkdir -p .sandbox-smoke
chmod 700 .sandbox-smoke
mkdir -p "$SBX_WORKSPACE"

SANDBOX_EXISTS="$(sbx ls --json | python3 -c "
import json, os, sys
# .get(...) or []: a machine with zero sandboxes may report null / omit the key.
sandboxes = json.load(sys.stdin).get('sandboxes') or []
name = os.environ['SBX_SANDBOX']
print('yes' if any(s['name'] == name for s in sandboxes) else 'no')
")"

if [[ "$SANDBOX_EXISTS" == "yes" ]]; then
  echo "Sandbox '$SBX_SANDBOX' already exists; reusing it."
else
  echo "Creating sandbox '$SBX_SANDBOX' at $SBX_WORKSPACE..."
  # `shell`: this smoke runs a plain Python script, not any of sbx's built-in
  # coding-agent environments (claude/codex/copilot/...). Verified against
  # `sbx create --help` / `sbx create shell --help` (sbx v0.34.0).
  sbx create --name "$SBX_SANDBOX" shell "$SBX_WORKSPACE"
fi

# Allowlist only the staging hosts and the package hosts needed to install the
# SDK-under-test, scoped to this sandbox alone (`--sandbox`, not the global
# policy). Both the REST and the WebSocket hostnames: a deployment may serve
# them from different hosts, and a missing WS allowlist entry would surface
# only later as an opaque probe timeout. One call, not one per host —
# `sbx policy allow network` takes a comma-separated list (verified against
# `sbx policy allow network --help`).
STAGING_HOSTS="$(uv run python -c "
import sys
sys.path.insert(0, '.')
from urllib.parse import urlparse
import probe
settings = probe.load_settings()
hosts = {
    urlparse(settings.endpoints.rest_url).hostname,
    urlparse(settings.endpoints.ws_url).hostname,
}
print(','.join(sorted(h for h in hosts if h)))
")"
sbx policy allow network --sandbox "$SBX_SANDBOX" \
  "$STAGING_HOSTS,pypi.org,files.pythonhosted.org"

echo "Copying agent.py into the sandbox workspace..."
cp "$(pwd)/agent.py" "$SBX_WORKSPACE/agent.py"

echo "Warming the sandbox's uv-managed environment for agent.py's dependencies..."
# `uv sync --script` resolves and installs agent.py's own PEP 723 environment —
# including its [tool.uv] override-dependencies (websockets>=16, required for
# the SDK's WebSocket to work through the sandbox proxy; see agent.py) — so
# this warms exactly the env `uv run agent.py` will use, not a lookalike.
sbx exec --workdir "$SBX_WORKSPACE" "$SBX_SANDBOX" uv sync --script agent.py

echo "Setup complete. Next: ./run.sh"
