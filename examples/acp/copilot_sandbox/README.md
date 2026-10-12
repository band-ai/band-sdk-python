# Copilot in a Docker sandbox (sbx) — over ACP stdio

Run the GitHub Copilot CLI inside a Docker **microVM sandbox** ([`sbx`](https://docs.docker.com/ai/sandboxes/))
and drive it from Band over `sbx exec -i <sandbox> copilot --acp` — the SDK's ordinary
**stdio** transport. No TCP, no socat, no port publishing, and **no SDK change**.

```
 host: client.py (Band SDK)  ──stdio──▶  sbx exec -i <sandbox> copilot --acp
                                          └── Copilot CLI in an isolated microVM
```

Why this variant:
- **Isolation** — Copilot runs in a microVM with its own filesystem + network.
- **Secret safety** — a host-side proxy injects the GitHub token at the network
  boundary; the token **never enters the sandbox**.
- **Auditable egress** — a default-deny firewall (`sbx policy log` shows every request).

> Validated: `sbx exec -i … copilot --acp` yields a byte-clean ACP/NDJSON stream (no
> PTY corruption) and the proxy authenticates the sandboxed agent. Use `sbx exec -i`,
> **not** `sbx run …` (that allocates a PTY and prepends `--yolo`).

## Prerequisites (one-time)

```bash
brew install docker/tap/sbx        # or see docs.docker.com/ai/sandboxes
sbx login                          # sign in to Docker (interactive)
sbx policy init balanced           # default-deny + common dev/GitHub/model APIs

# Store the GitHub token on the host — proxy-injected, never exposed in the VM:
gh auth token | sbx secret set -g github
```

You also need a configured Band agent named `copilot_acp_agent` (see the SDK's
`Agent.from_config` / `agent_config.yaml`).

Create the sandbox with the Band MCP kit below, or configure a reachable external
Band MCP server. SDK 5 requires Band tools for replies and explicit declines;
native assistant text is optional thought telemetry. Set `BAND_MCP_SSE_URL` to
the server's address inside the sandbox. The client rejects a missing setting.

## Run

```bash
cp examples/acp/copilot_sandbox/.env.example examples/acp/copilot_sandbox/.env
# Set SBX_SANDBOX, SBX_WORKSPACE and BAND_MCP_SSE_URL in that file.
uv run examples/acp/copilot_sandbox/client.py
```

Then message the `copilot_acp_agent` from a Band room — Copilot handles the turn
inside the sandbox.

## Band tools with the kit

To give Copilot Band tools without exposing a host service, create the sandbox
with the included Docker sandbox kit:

```bash
sbx kit validate examples/acp/copilot_sandbox/band-mcp-kit

# Store the Band agent key outside the VM. The sandbox sees only `proxy-managed`;
# Docker's proxy replaces that placeholder on requests to app.band.ai.
sbx secret set-custom -g \
  --host app.band.ai \
  --env BAND_AGENT_KEY \
  --placeholder proxy-managed \
  --value "$BAND_AGENT_KEY"

sbx create \
  --name copilot-band \
  --kit examples/acp/copilot_sandbox/band-mcp-kit \
  copilot \
  /path/to/workspace

export BAND_MCP_SSE_URL=http://127.0.0.1:3000/sse
uv run examples/acp/copilot_sandbox/client.py
```

The kit installs `band-mcp`, starts it on `127.0.0.1:3000` inside the sandbox, and
uses Docker's custom-secret flow for `BAND_AGENT_KEY`. It targets
`https://app.band.ai`; for a different Band deployment, update the kit and the
`sbx secret set-custom --host` value.

`BAND_AGENT_KEY` must be the same agent key as `copilot_acp_agent` in
`agent_config.yaml`, so tool replies and the host runtime use one Band identity.

## Band tools with an external MCP server

You can also run `band-mcp` outside the sandbox. The sandbox's egress firewall blocks
the SDK host's loopback, so the in-process `LocalMCPServer` is unreachable. To use an
external Band MCP server:

1. Run a reachable `band-mcp` (SSE) server — see `../copilot_docker/` — bound to a
   routable interface (not loopback).
2. Allow the sandbox to reach it and address it via `host.docker.internal`:
   ```bash
   sbx policy allow network host.docker.internal:8002
   ```
3. Point the adapter at it: `inject_band_tools=False` +
   `mcp_servers=[{"type": "sse", "name": "band", "url": "http://host.docker.internal:8002/sse", "headers": []}]`.

`sbx policy log` shows whether the tool calls are being forwarded or blocked.

## Design notes (verified against sbx 0.34.0 / copilot 1.0.65)

- **Transport:** `sbx exec -i` mirrors `docker exec -i` — `-i` keeps STDIN open, `-t`
  (PTY) is a **separate** flag we deliberately omit, so stdio stays raw for NDJSON.
- **Auth:** stored via `sbx secret set -g github` (a fine-grained PAT with "Copilot
  Requests", or a Copilot/`gh` OAuth token; classic `ghp_` is rejected). The subprocess
  env carries no token — sbx's proxy handles it.
- **cwd:** each ACP `new_session` needs an absolute cwd that exists *inside* the
  sandbox; with the default direct mount that's the same path as the host workspace.

> Deployment template — needs `sbx`, Docker, a Band agent, and Copilot auth; not run in CI.
