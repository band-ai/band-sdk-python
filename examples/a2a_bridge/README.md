# A2A Bridge Examples

Examples showing how to connect a remote A2A agent to Band through the A2A bridge adapter.

## Why the bridge exists

Band rooms speak Band's platform API and WebSocket event model. Remote A2A agents speak A2A.

The bridge lets a remote A2A agent behave like a normal Band room participant:

- the bridge connects to Band as a platform agent
- it receives room messages and room lifecycle events
- it forwards user messages to a remote A2A agent
- it posts the A2A agent's replies back into the room
- it persists `context_id`, `task_id`, and task state so a conversation can resume after reconnect

Without the bridge, a remote A2A agent is not automatically a bidirectional Band room participant.

## What these examples cover

| File | Description |
|------|-------------|
| `01_basic_agent.py` | **Basic bridge** - Connect a remote A2A agent to Band with no A2A auth. |
| `02_with_auth.py` | **Authenticated bridge** - Same bridge flow, but with API key, bearer token, or custom auth headers for the remote A2A agent. |

## What was validated during `INT-245`

- `01_basic_agent.py` worked live end to end
- session persistence worked live across restart
- `02_with_auth.py` originally failed with `401 Unauthorized`
- the auth path is fixed in the adapter so auth is applied during:
  - remote agent-card discovery
  - outbound A2A RPC calls
- `02_with_auth.py` now passes end to end against a local auth-protected A2A server, with a real Band room message forwarded through the bridge and echoed back
- the README command shape was re-run from a clean checkout after switching the examples to the local repo source

## Prerequisites

### Band setup

You need:

- `.env` with `BAND_WS_URL` and `BAND_REST_URL`
- `agent_config.yaml` with credentials for `a2a_agent`

The config name must be exactly `a2a_agent`.

Minimal `agent_config.yaml` entry:

```yaml
a2a_agent:
  agent_id: "your-a2a-bridge-agent-id"
  api_key: "your-a2a-bridge-agent-api-key"
```

### Remote A2A agent setup

You also need a reachable A2A server. For example:

```bash
export A2A_AGENT_URL=http://localhost:10000
```

Before starting the bridge, confirm the remote agent card is reachable:

```bash
curl http://localhost:10000/.well-known/agent.json
```

or:

```bash
curl http://localhost:10000/.well-known/agent-card.json
```

## Install

From the repo root:

```bash
uv sync --extra a2a
```

When you run these examples from this repository, `uv run` uses the local checkout of `band-sdk`.

## Running the examples

### 1. Basic bridge

Use this if the remote A2A agent does not require auth.

```bash
export A2A_AGENT_URL=http://localhost:10000
uv run examples/a2a_bridge/01_basic_agent.py
```

### 2. Authenticated bridge

Use this if the remote A2A agent requires credentials.

Supported auth inputs:

- `A2A_API_KEY`
  Sends `X-API-Key`
- `A2A_BEARER_TOKEN`
  Sends `Authorization: Bearer ...`
- `A2A_AUTH_HEADERS_JSON`
  Sends any custom header map you need

Examples:

```bash
export A2A_AGENT_URL=http://localhost:10000
export A2A_API_KEY=your-api-key
uv run examples/a2a_bridge/02_with_auth.py
```

```bash
export A2A_AGENT_URL=http://localhost:10000
export A2A_BEARER_TOKEN=your-token
uv run examples/a2a_bridge/02_with_auth.py
```

```bash
export A2A_AGENT_URL=http://localhost:10000
export A2A_AUTH_HEADERS_JSON='{"X-Custom-Auth":"value"}'
uv run examples/a2a_bridge/02_with_auth.py
```

## End-to-end smoke test

Once the bridge is running:

1. Create a Band room
2. Add the bridge agent to the room
3. Send a message in the room
4. Confirm the remote A2A agent replies in the same room

What success looks like:

- the bridge process stays connected
- the remote A2A agent receives the forwarded message
- the reply appears back in the Band room

## Session persistence test

The bridge persists A2A session state by writing task metadata into Band history.

To test that:

1. send a first message in the room
2. confirm the remote A2A agent replies
3. stop the bridge process
4. restart the same bridge example
5. send another message in the same room
6. confirm the same remote conversation continues

What success looks like:

- the bridge restores the previous `context_id`
- the next response continues the same A2A conversation instead of starting over

## How auth works

After the `INT-245` fix, `A2AAuth` is applied to both parts of the client flow:

1. agent-card discovery
2. JSON-RPC message calls to the remote agent

That matters because an auth-protected agent can fail before the first message is ever sent if discovery is unauthenticated.

## Common problems

### The bridge starts but the remote A2A agent never replies

Check:

- `A2A_AGENT_URL` is correct
- the remote agent is actually running
- the bridge agent was added to the Band room

### `401 Unauthorized`

Check:

- you are using the right auth mode
- `A2A_API_KEY` matches the server's expected header scheme
- if the server expects a custom header name, use `A2A_AUTH_HEADERS_JSON`

### `agent_config.yaml` is not found

Run the example from the repo root so `load_agent_config()` can find the file.

### The example is not using your local branch changes

Run it from the repo root with the checked-in script metadata intact:

```bash
uv run examples/a2a_bridge/01_basic_agent.py
```

That command now uses the local checkout of `band-sdk`.

### The conversation restarts after reconnect

Make sure you restarted the same bridge agent and sent the follow-up message in the same Band room.
