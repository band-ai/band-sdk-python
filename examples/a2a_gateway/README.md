# A2A Gateway Examples

Examples showing how to expose Band peers as inbound A2A endpoints.

## Why the gateway exists

The gateway is the inbound side of the Band A2A story.

- the **bridge** connects a remote A2A agent into Band
- the **gateway** exposes Band peers out to remote A2A clients

A remote A2A client can call the gateway, discover Band peers through AgentCard routes, and send requests that the gateway turns into Band room activity.

## Architecture

```text
Remote A2A Client
        |
        v
   A2A Gateway
        |
        v
 Band Platform
        |
        v
   Band Peer
```

Context is preserved by mapping the incoming A2A `contextId` to a Band room.

## What these examples cover

| File | Description |
|------|-------------|
| `01_basic_gateway.py` | **Basic gateway** - Start the gateway and expose Band peers as A2A endpoints. |
| `02_with_demo_agent.py` | **Gateway plus orchestrator** - Run the gateway and a local demo A2A agent that calls gateway peers. |
| `demo_orchestrator/` | Implementation details for the local demo orchestrator agent. |

## What was validated during `INT-245`

- `01_basic_gateway.py` worked live for startup and `/peers`
- per-peer agent card discovery worked live
- a deterministic Band peer completed a real gateway round-trip
- the gateway reused the same room when the same `context_id` was used
- the README startup flow was re-run after fixing credential selection and local source execution
- `02_with_demo_agent.py` was exercised as the higher-level gateway demo path
- I stopped late reruns once the platform started returning `429` responses on the gateway account, rather than treating that throttling as a gateway SDK failure

## Prerequisites

You need:

- `.env` or `agent_config.yaml` with Band credentials
- at least one Band peer available in the workspace
- `OPENAI_API_KEY` if you want to run the demo orchestrator

Recommended: use `gateway_agent` in `agent_config.yaml`.
The config name must be exactly `gateway_agent`.

```yaml
gateway_agent:
  agent_id: "your-gateway-agent-id"
  api_key: "your-gateway-api-key"
```

Fallback: you can also use environment variables:

```bash
export BAND_API_KEY=your-band-api-key
export BAND_AGENT_ID=a2a-gateway
```

## Install

From the repo root:

```bash
uv sync --extra a2a_gateway
```

If you want the orchestrator demo too:

```bash
uv sync --extra a2a_gateway_demo
```

When you run these examples from this repository, `uv run` uses the local checkout of `band-sdk`.

## Running the basic gateway

```bash
uv run examples/a2a_gateway/01_basic_gateway.py
```

Once it is up, the most useful endpoints are:

- `GET http://localhost:10000/peers`
- `GET http://localhost:10000/agents/<peer-id>/.well-known/agent.json`
- `POST http://localhost:10000/agents/<peer-id>`
- `POST http://localhost:10000/agents/<peer-id>/v1/message:stream`

## Basic smoke test

### 1. Start the gateway

```bash
uv run examples/a2a_gateway/01_basic_gateway.py
```

### 2. List peers

```bash
curl http://localhost:10000/peers
```

Pick one peer id from the response.

### 3. Fetch the peer's agent card

```bash
curl http://localhost:10000/agents/<peer-id>/.well-known/agent.json
```

### 4. Send a message

Use a stable `contextId` so you can test room reuse.

For JSON-RPC clients, send requests to the base agent route:

```bash
curl -X POST http://localhost:10000/agents/<peer-id> \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "Hello from an A2A client"}],
        "messageId": "msg-1",
        "contextId": "ctx-1"
      }
    }
  }'
```

If you want the legacy streaming route, send a raw A2A `Message` body instead:

```bash
curl -N -X POST http://localhost:10000/agents/<peer-id>/v1/message:stream \
  -H "Content-Type: application/json" \
  -d '{
    "role": "user",
    "parts": [{"kind": "text", "text": "Hello from an A2A client"}],
    "messageId": "msg-1",
    "contextId": "ctx-1"
  }'
```

What success looks like:

- the gateway accepts the request
- the mapped Band peer replies
- reusing `contextId: "ctx-1"` continues the same conversation

## Context reuse test

To verify the same `contextId` maps back to the same Band room:

1. send a first request with `contextId: "ctx-1"`
2. send a second request with the same `contextId`
3. confirm the peer continues the same conversation instead of starting a new room

This is one of the main gateway behaviors worth checking during integration work.

## Running the demo orchestrator

This example starts:

- the gateway on port `10000`
- a local demo A2A orchestrator on port `10001`

Set the model key first:

```bash
export OPENAI_API_KEY=your-openai-key
uv run examples/a2a_gateway/02_with_demo_agent.py
```

Then check the orchestrator card:

```bash
curl http://localhost:10001/.well-known/agent.json
```

The orchestrator's job is to accept an incoming A2A request and route it to one of the peers exposed by the gateway.

## What success looks like

### Basic gateway

- `/peers` returns real Band peers
- `/.well-known/agent.json` returns a valid card for a peer
- a message call returns a peer response
- the same `contextId` reuses the same room

### Demo orchestrator

- the orchestrator card is reachable on port `10001`
- a request to the orchestrator is routed through the gateway
- the orchestrator returns the downstream peer response

## Common problems

### `/peers` is empty

Check that:

- your Band credentials are valid
- the workspace actually has peers to expose

### The gateway fails with a permissions error

Use an agent-scoped credential, not a user-scoped API key.

Use `gateway_agent` from `agent_config.yaml`, which is the safest path for onboarding.

### Missing `OPENAI_API_KEY`

`02_with_demo_agent.py` fails fast with `ValueError` when the orchestrator model key is missing.

### Confusing the bridge and the gateway

Use:

- the **bridge** when you want a remote A2A agent to join Band rooms
- the **gateway** when you want a remote A2A client to talk to Band peers

### Requests work but context does not persist

Make sure you are reusing the same `contextId` value between requests.

### Port `10000` is already in use

Pick another port for the local run:

```bash
GATEWAY_PORT=10002 GATEWAY_URL=http://localhost:10002 uv run examples/a2a_gateway/01_basic_gateway.py
```
