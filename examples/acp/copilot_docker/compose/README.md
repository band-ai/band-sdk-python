# Copilot over ACP — Docker Compose (multi-service)

GitHub Copilot and the Band-tools MCP server as two long-running compose
services. For every Band room the SDK starts its own `copilot --acp` inside the
`copilot` service (`docker compose exec -T`) and speaks ACP over that process's
stdio. Copilot calls Band tools on the separate `band-mcp` service.

```
 host: client.py (Band SDK)
   room A ──stdio──▶ docker compose exec -T copilot copilot --acp ─┐ one process
   room B ──stdio──▶ docker compose exec -T copilot copilot --acp ─┘ per room
 copilot (container)  ──SSE──▶  band-mcp:3000  (compose network only)
```

Nothing is published to the host: the SDK reaches Copilot through `docker
compose exec`, and only Copilot dials `http://band-mcp:3000/sse`, resolved over
the compose network.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Two services: `copilot` (idle, exec'd into per room) + `band-mcp` (Band tools over SSE) |
| `Dockerfile.copilot` | Copilot CLI image |
| `Dockerfile.band-mcp` | `band-mcp` SSE server |
| `client.py` | Host-side Band agent: one `docker compose exec -T` per room, `inject_band_tools=False`, explicit MCP URL |
| `.env.example` | Required secrets/endpoints |

## Prerequisites

- Docker + Docker Compose.
- A **Copilot-entitled** `GITHUB_TOKEN`.
- A configured Band agent named `copilot_acp_agent` in `agent_config.yaml`.
  Put that agent's `api_key` into `.env` as `BAND_AGENT_KEY` — host and band-mcp
  must be the same identity (room tools 404 otherwise).

## Run

```bash
cd examples/acp/copilot_docker/compose
cp .env.example .env
# Fill GITHUB_TOKEN, BAND_AGENT_KEY (= copilot_acp_agent api_key from
# agent_config.yaml) and COPILOT_WORKSPACES (an absolute, symlink-free path)
docker compose up -d --build

# from the repo root:
uv run examples/acp/copilot_docker/compose/client.py
```

Then message the `copilot_acp_agent` from a Band room; Copilot handles the turn
and calls Band tools via band-mcp.

## Design notes / gotchas (verified against the shipped tools)

- **One process per room, over stdio.** The SDK gives every room its own ACP
  process, so a room's turns never share Copilot's memory or tool state with
  another room's. `exec -T` disables the pseudo-TTY so stdin/stdout stay raw
  pipes — byte-clean for ACP's newline-delimited JSON. When the client stops, its
  `copilot --acp` processes end; the service keeps idling.
- **Workspaces.** The SDK creates `<COPILOT_WORKSPACES>/<room-id>` on the host and
  uses it as the session cwd, and compose mounts `COPILOT_WORKSPACES` into the
  `copilot` service at the same path. The SDK resolves symlinks in room paths, so
  `client.py` refuses a `COPILOT_WORKSPACES` that isn't already resolved (on macOS,
  `/tmp` is a symlink to `/private/tmp`).
- **band-mcp uses SSE, not streamable HTTP.** The endpoint is `/sse`; the adapter's
  `mcp_servers` entry is `{"type": "sse", …}`.
- **band-mcp version.** `Dockerfile.band-mcp` installs `band-mcp>=2.2.2`; earlier
  2.x releases on PyPI do not install or import cleanly. Pin another release with
  the `BAND_MCP_SPEC` build arg.
- **DNS-rebinding protection.** band-mcp rejects SSE requests with **HTTP 421**
  unless the caller's `Host` is allow-listed. `docker-compose.yml` sets
  `ALLOWED_HOSTS='["band-mcp:*"]'` (the compose-DNS name Copilot dials). Add your
  own host there if you change the service name.
- **Auth model.** band-mcp holds one Band identity (`BAND_AGENT_KEY`) and MCP
  clients present **no** credentials. That key must be the same agent as host
  `client.py` (`copilot_acp_agent` in `agent_config.yaml`), which checks it.
  Treat band-mcp as a trusted sidecar — it is not published to the host.
- **Copilot auth.** The Copilot CLI checks `COPILOT_GITHUB_TOKEN`, then
  `GH_TOKEN`, then `GITHUB_TOKEN`, or uses a stored `copilot login`. A container
  has no stored login, so set a token env (a v2 fine-grained PAT with "Copilot
  Requests", or a Copilot/`gh` OAuth token — classic `ghp_` and Actions `ghs_`
  tokens are rejected).
- **Tool approval.** `client.py` runs `copilot --acp --allow-all-tools` so
  Copilot's built-in tools run unattended in the container (Band/MCP tools are
  approved through ACP). Drop the flag to gate built-in shell/file tools; note
  enterprise policy can disable allow-all flags at startup.
- **Room routing.** band-mcp's chat/message tools take a `chat_id` argument per
  call (scoped within that one identity), so the adapter states the room's
  `chat_id` in each session's first prompt. The SDK's `inject_band_tools` path
  binds each session to its room's endpoint instead, so its tools take none.
- **Platform base URL.** band-mcp (`BAND_BASE_URL`) defaults to `https://app.band.ai`;
  the compose file points it at `BAND_REST_URL` (default `https://app.band.ai`).

> This example is a deployment template — it needs Docker, live Band credentials,
> and a Copilot-entitled token, so it is not run in CI.
