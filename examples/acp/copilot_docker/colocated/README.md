# Copilot over ACP — colocated (one container per room)

GitHub Copilot **and** `band-mcp` in one image. The Band SDK starts one container
per Band room with `docker run -i --rm` and speaks ACP over that container's
stdio. Copilot reaches Band tools over the container's own loopback; nothing is
published. This is the self-contained "just run this image" unit.

```
 host: client.py (Band SDK)
   room A ──stdio──▶ docker run -i --rm copilot-band-acp ─┐
   room B ──stdio──▶ docker run -i --rm copilot-band-acp  │ one container each
 ┌─ container (per room) ───────────────────────────────┐ │
 │  copilot --acp   ──SSE──▶  127.0.0.1:3000 (band-mcp)  │◀┘
 │  /…/workspaces/<room-id>   (bind-mounted, same path)   │
 └──────────────────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Node (Copilot CLI) + Python venv (`band-mcp`), one image |
| `entrypoint.sh` | Starts band-mcp on loopback, then runs `copilot --acp` on stdio |
| `client.py` | Host-side Band agent: one `docker run -i` per room, `inject_band_tools=False`, loopback MCP URL |
| `.env.example` | Required secrets/endpoints |

## Prerequisites

- Docker.
- A **Copilot-entitled** `GITHUB_TOKEN`.
- A configured Band agent named `copilot_acp_agent` in `agent_config.yaml`.
  Put that agent's `api_key` into `.env` as `BAND_AGENT_KEY` — host and band-mcp
  must be the same identity (room tools 404 otherwise).

## Run

```bash
cd examples/acp/copilot_docker/colocated
cp .env.example .env
# Fill GITHUB_TOKEN and BAND_AGENT_KEY (= copilot_acp_agent api_key from agent_config.yaml)
docker build -t copilot-band-acp .

# from the repo root:
uv run examples/acp/copilot_docker/colocated/client.py
```

Then message the `copilot_acp_agent` from a Band room. The first message in a
room starts its container; it stops when the client does.

## Design notes / gotchas (verified against the shipped tools)

- **One container per room, over stdio.** The SDK gives every room its own ACP
  process, so a room's turns never share Copilot's memory or tool state with
  another room's. `docker run -i` (no `-t`) keeps stdin open with raw pipes —
  byte-clean for ACP's newline-delimited JSON.
- **stdout is the ACP channel.** Anything else the container prints to stdout
  corrupts the protocol, so `entrypoint.sh` sends band-mcp's output to stderr.
- **Workspaces.** The SDK creates `<COPILOT_WORKSPACES>/<room-id>` on the host and
  uses it as the session cwd; `client.py` bind-mounts the root at the same path,
  so the directory exists inside every room's container. Defaults to
  `workspaces/` next to `client.py`.
- **band-mcp uses SSE, not streamable HTTP** (`/sse`); the adapter's `mcp_servers`
  entry is `{"type": "sse", …}`. Copilot connects to it while creating the session,
  so `entrypoint.sh` waits for band-mcp to listen before starting Copilot.
- **band-mcp version.** The image installs `band-mcp>=2.2.2`; earlier 2.x releases
  on PyPI do not install or import cleanly. Pin another release with
  `--build-arg BAND_MCP_SPEC=…`.
- **DNS-rebinding protection.** band-mcp 421s SSE requests whose `Host` isn't
  allow-listed. `entrypoint.sh` sets `ALLOWED_HOSTS='["localhost:*","127.0.0.1:*"]'`
  for the in-container loopback caller.
- **Auth model.** band-mcp holds one Band identity (`BAND_AGENT_KEY`); MCP
  clients present no credentials. That key must be the same agent as host
  `client.py` (`copilot_acp_agent` in `agent_config.yaml`), which checks it.
- **Copilot auth.** The Copilot CLI checks `COPILOT_GITHUB_TOKEN`, then
  `GH_TOKEN`, then `GITHUB_TOKEN`, or uses a stored `copilot login`. A container
  has no stored login, so set a token env (v2 fine-grained PAT with "Copilot
  Requests", or a Copilot/`gh` OAuth token — classic `ghp_` / Actions `ghs_`
  tokens are rejected).
- **Tool approval.** `entrypoint.sh` runs `copilot --acp --allow-all-tools` so
  built-in tools run unattended in the throwaway container (Band/MCP tools are
  approved through ACP). Drop the flag to gate built-in tools.
- **Platform base URL.** band-mcp (`BAND_BASE_URL`) defaults to `https://app.band.ai`;
  `entrypoint.sh` points it at `BAND_REST_URL` (same default).

## Compose vs colocated

Use **this** image for the simplest unit: each room gets a fresh container. Use
[`../compose/`](../compose/) when Copilot and band-mcp should be long-running,
separately scalable services.

> This example is a deployment template — it needs Docker, live Band credentials,
> and a Copilot-entitled token, so it is not run in CI.
