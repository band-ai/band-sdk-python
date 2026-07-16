# Copilot over ACP — colocated (single container)

GitHub Copilot **and** `band-mcp` in one image, driven by the Band SDK over
**TCP**. Copilot reaches Band tools over the container's own loopback; only the
ACP port is published. This is the self-contained "just run this container" unit —
simplest networking, one image, no cross-service DNS.

```
 host: client.py (Band SDK)  ──TCP──▶  container:8080  (published)
 ┌─ container ────────────────────────────────────────┐
 │  socat 0.0.0.0:8080  ──stdio──▶  copilot --acp      │
 │  copilot             ──SSE────▶  127.0.0.1:3000     │
 │                                   (band-mcp)         │
 └──────────────────────────────────────────────────── ┘
```

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Node (Copilot CLI) + Python venv (`band-mcp`) + `socat`, one image |
| `entrypoint.sh` | Starts band-mcp on loopback, then fronts `copilot --acp` on TCP `0.0.0.0:8080` |
| `client.py` | Host-side Band agent: TCP to Copilot, `inject_band_tools=False`, loopback MCP URL |
| `.env.example` | Required secrets/endpoints |

## Prerequisites

- Docker.
- A **Copilot-entitled** `GITHUB_TOKEN`.
- A Band **agent** API key (`BAND_AGENT_KEY`) for the in-container band-mcp.
- A configured Band agent named `copilot_acp_agent` for the host client.

## Run

```bash
cd examples/acp/copilot_docker/colocated
cp .env.example .env         # fill in GITHUB_TOKEN + BAND_AGENT_KEY
docker build -t copilot-band-acp .
docker run --rm --env-file .env -p 127.0.0.1:8080:8080 copilot-band-acp

# in another shell, from the repo root:
uv run examples/acp/copilot_docker/colocated/client.py
```

`client.py` uses `/` as Copilot's working directory by default because the ACP
server runs in a container. Set `COPILOT_ACP_CWD` to another path only when it
exists inside the Copilot container.

Then message the `copilot_acp_agent` from a Band room.

## Design notes / gotchas (verified against the shipped tools)

- **Why socat, not `copilot --acp --port`.** `copilot --acp --port <N>` binds
  `127.0.0.1` only (no host-bind flag), unreachable through Docker port publishing.
  `socat TCP-LISTEN:8080,fork EXEC:"copilot --acp"` fronts the documented stdio ACP
  server on a routable port — the endpoint `CopilotACPAdapter(host=…, port=…)` dials.
- **Fresh process per connection.** `,fork` execs a new `copilot --acp` for each TCP
  connection, so a reconnect (e.g. an `ACPRuntime` respawn) lands on a process with no
  prior in-memory sessions. The SDK uses ACP's session-load capability before trusting
  a persisted ID; unsupported or unavailable sessions get a fresh session before the
  prompt is sent. Earlier Copilot conversation state is not resumed after a reconnect.
- **Bind loopback.** The `docker run -p 127.0.0.1:8080:8080` above keeps the ACP port
  on the host loopback. Copilot here is unauthenticated and runs `--allow-all-tools`,
  so expose it off-host only behind your own auth.
- **band-mcp uses SSE, not streamable HTTP** (`/sse`); the adapter's `mcp_servers`
  entry is `{"type": "sse", …}`.
- **DNS-rebinding protection.** band-mcp 421s SSE requests whose `Host` isn't
  allow-listed. `entrypoint.sh` sets `ALLOWED_HOSTS='["localhost:*","127.0.0.1:*"]'`
  for the in-container loopback caller.
- **Auth model.** band-mcp holds one Band identity (its agent key); MCP clients
  present no credentials. Colocation keeps band-mcp bound to loopback and never
  published — it is unreachable from outside the container.
- **Copilot auth.** The Copilot CLI checks `COPILOT_GITHUB_TOKEN`, then
  `GH_TOKEN`, then `GITHUB_TOKEN`, or uses a stored `copilot login`. A container
  has no stored login, so set a token env (v2 fine-grained PAT with "Copilot
  Requests", or a Copilot/`gh` OAuth token — classic `ghp_` / Actions `ghs_`
  tokens are rejected).
- **Tool approval.** `entrypoint.sh` runs `copilot --acp --allow-all-tools` so
  built-in tools run unattended in this isolated container (Band/MCP tools are
  auto-approved via the ACP handler). Drop the flag to gate built-in tools.
- **Room routing.** band-mcp chat/message tools take a `chat_id` argument per call.
- **Platform base URL.** band-mcp (`BAND_BASE_URL`) defaults to `https://app.band.ai`;
  `entrypoint.sh` points it at `BAND_REST_URL` (same default) via that variable.

## Compose vs colocated

Use **this** single-container image for the simplest "one unit" deployment. Use
[`../compose/`](../compose/) when Copilot and
band-mcp should be independent, separately scalable services on a shared network.

> This example is a deployment template — it needs Docker, live Band credentials,
> and a Copilot-entitled token, so it is not run in CI.
