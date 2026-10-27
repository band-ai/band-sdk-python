# One-shot HTTP hosting

The canonical `server.py` runs `OneShotInvoker` with the Anthropic adapter.
It handles `GET /ping` after startup and `POST /invocations` with the bridge's
unchanged event envelope. The SDK owns history, claims and completion.

Use the [Cloudflare example](../cloudflare/README.md) for a container per room,
or [AgentCore](../agentcore/README.md) for AWS hosting. A persistent bridge
on your own machine or server holds the Band WebSocket; the receiver uses REST.

The launcher requires this complete checkout: `bridge_core` is a repository
application outside the published SDK wheel. Its script metadata installs the
released SDK's bridge dependencies, including the AgentCore transport.

Configure `BAND_BRIDGE_AGENTS` and matching `BAND_REST_URL` / `BAND_WS_URL` in
repo-root `.env.test`, then run directly from the checkout:

```sh
uv run --isolated examples/oneshot/run-bridge.py
```

Do not invoke the script through `uv run python`: that bypasses its declared
script dependencies. The receiver can similarly run directly with `uv run
--isolated examples/oneshot/server.py` when its credentials are supplied.

HTTP targets optionally accept `bearer_token`. An explicitly empty token is
invalid. HTTP and AgentCore target options reject unknown fields, so fix typos
instead of relying on ignored configuration. `messages_only` defaults to false;
keep that default for AgentCore so room cleanup reaches the adapter. Cloudflare
opts in because its room-local container termination bounds the adapter lifetime.
