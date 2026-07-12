# Band MCP sandbox kit

Mixin kit for the `copilot_sandbox` example. It installs `band-mcp` and starts
its SSE endpoint on the sandbox loopback. Store the Band agent key as a Docker
custom secret with the same `proxy-managed` placeholder that the kit sets in
`BAND_AGENT_KEY`. The real key stays on the host.

## Use

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

Then message the configured `copilot_acp_agent` in Band. Copilot runs inside the
sandbox and calls Band tools through `band-mcp` on `127.0.0.1:3000`.

## Notes

- This is a local-sandbox deployment example, not a CI fixture.
- The kit targets `https://app.band.ai`. For another Band deployment, update
  `caps.network.allow`, `THENVOI_BASE_URL`, and the `--host` passed to
  `sbx secret set-custom`.
- `band-mcp` is one trusted Band identity. Keep it bound to loopback, as this kit
  does, unless you add your own network and auth controls.
- Run `sbx policy log` if installs or tool calls are blocked.
