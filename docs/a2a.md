# A2A Protocol Integration

The SDK supports the [A2A (Agent-to-Agent) protocol](https://google.github.io/A2A/) in two directions:

## A2A Adapter (outbound)

`A2AAdapter` forwards Band messages to a remote A2A-compliant agent. Each Band room maps to an A2A context, with automatic session state persistence via task events and session rehydration on room rejoin.

```python
from band.adapters.a2a import A2AAdapter, A2AAuth

adapter = A2AAdapter(
    remote_url="http://localhost:10000",
    auth=A2AAuth(api_key="..."),  # optional
)
```

## A2A Gateway (inbound)

`A2AGatewayAdapter` + `GatewayServer` expose Band peers as A2A JSON-RPC endpoints. Remote A2A clients can send messages to Band agents via the gateway, with context ID preservation (same `contextId` = same chat room) and SSE streaming responses.

```python
from band.adapters.a2a_gateway import A2AGatewayAdapter, GatewayServer

adapter = A2AGatewayAdapter(port=10000)
```

## Key files

| Purpose | Path |
|---|---|
| A2A Adapter | `src/band/adapters/a2a.py`, `src/band/integrations/a2a/adapter.py` |
| A2A Gateway | `src/band/adapters/a2a_gateway.py`, `src/band/integrations/a2a/gateway/` |
| A2A Types | `src/band/integrations/a2a/types.py` |
