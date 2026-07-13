# Copilot over ACP — Docker Compose (multi-service)

GitHub Copilot in a container, driven by the Band SDK over **TCP**, with Band
tools served by a **separate `band-mcp` service** on the same compose network.
This is the cloud-style topology: Copilot and the Band-tools MCP server are
independent, separately scalable services.

```
 host: client.py (Band SDK)  ──TCP──▶  copilot:8080     (published to host)
 copilot (container)         ──SSE──▶  band-mcp:3000    (compose network only)
```

The SDK on the host connects to Copilot at `localhost:8080`. Copilot reaches Band
tools at `http://band-mcp:3000/sse`, resolved over the compose network — the host
process never resolves that name.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Two services: `copilot` (ACP over TCP) + `band-mcp` (Band tools over SSE) |
| `Dockerfile.copilot` | Copilot CLI + `socat` bridging `copilot --acp` (stdio) onto TCP `0.0.0.0:8080` |
| `Dockerfile.band-mcp` | `pip install band-mcp`, run the `thenvoi-mcp` SSE server |
| `client.py` | Host-side Band agent: TCP to Copilot, `inject_band_tools=False`, explicit MCP URL |
| `.env.example` | Required secrets/endpoints |

## Prerequisites

- Docker + Docker Compose.
- A **Copilot-entitled** `GITHUB_TOKEN`.
- A Band **agent** API key (`BAND_AGENT_KEY`, `thnv_a_…` / `band_a_…`) for band-mcp.
- A configured Band agent named `copilot_acp_agent` for the host client (see the
  SDK's `Agent.from_config` / `agent_config.yaml`).

## Run

```bash
cd examples/acp/copilot_docker/compose
cp .env.example .env      # fill in GITHUB_TOKEN + BAND_AGENT_KEY
docker compose up --build # starts copilot (:8080) + band-mcp (internal :3000)

# in another shell, from the repo root:
uv run examples/acp/copilot_docker/compose/client.py
```

`client.py` uses `/` as Copilot's working directory by default because the ACP
server runs in a container. Set `COPILOT_ACP_CWD` to another path only when it
exists inside the Copilot container.

Then message the `copilot_acp_agent` from a Band room; Copilot handles the turn
and calls Band tools via band-mcp.

## Design notes / gotchas (verified against the shipped tools)

- **Why socat, not `copilot --acp --port`.** `copilot --acp --port <N>` binds
  `127.0.0.1` only (no host-bind flag), which Docker port publishing cannot reach.
  `socat TCP-LISTEN:8080,fork EXEC:"copilot --acp"` fronts the documented stdio ACP
  server on a routable port — and is exactly the TCP endpoint the SDK's
  `CopilotACPAdapter(host=…, port=…)` dials.
- **Fresh process per connection.** `,fork` execs a new `copilot --acp` for each TCP
  connection, so a reconnect (e.g. an `ACPRuntime` respawn) lands on a process with no
  prior in-memory sessions. Harmless here — Band rehydrates session state from room
  history — but don't expect Copilot's native ACP session resume to survive a reconnect.
- **Bind loopback.** `docker-compose.yml` publishes the ACP port as
  `127.0.0.1:8080:8080`: an unauthenticated, allow-all `copilot --acp` must not be
  reachable off-host. Widen it (behind your own auth) only for a remote SDK host.
- **band-mcp uses SSE, not streamable HTTP.** The endpoint is `/sse`; the adapter's
  `mcp_servers` entry is `{"type": "sse", …}`.
- **DNS-rebinding protection.** band-mcp rejects SSE requests with **HTTP 421**
  unless the caller's `Host` is allow-listed. `docker-compose.yml` sets
  `ALLOWED_HOSTS='["band-mcp:*"]'` (the compose-DNS name Copilot dials). Add your
  own host there if you change the service name, or set
  `ENABLE_DNS_REBINDING_PROTECTION=false` for local experiments.
- **Auth model.** band-mcp holds one Band identity (its agent key) and MCP clients
  present **no** credentials. Treat band-mcp as a trusted sidecar — it is not
  published to the host here. One container = one Band identity.
- **Copilot auth.** The Copilot CLI authenticates from `GITHUB_TOKEN` /
  `GH_TOKEN` / `COPILOT_GITHUB_TOKEN` (checked in that reverse order) **or** a
  stored `copilot login`. A container has no stored login, so set a token env
  (a v2 fine-grained PAT with "Copilot Requests", or a Copilot/`gh` OAuth token —
  classic `ghp_` and Actions `ghs_` tokens are rejected).
- **Tool approval.** `Dockerfile.copilot` runs `copilot --acp --allow-all-tools`
  so Copilot's built-in tools run unattended in this isolated container (Band/MCP
  tools are already auto-approved via the ACP `request_permission` handler). Drop
  the flag to gate built-in shell/file tools; note enterprise policy can disable
  allow-all flags at startup.
- **Room routing.** band-mcp's chat/message tools take a `chat_id` argument per
  call (scoped within that one identity). This differs from the SDK's in-process
  `inject_band_tools` path (which injects a `room_id` per tool) — expect the agent
  to reference `chat_id` when driven through band-mcp.
- **Platform base URL.** band-mcp defaults to `https://app.thenvoi.com`; the
  compose file points it at `BAND_REST_URL` (default `https://app.band.ai`).

> This example is a deployment template — it needs Docker, live Band credentials,
> and a Copilot-entitled token, so it is not run in CI.
