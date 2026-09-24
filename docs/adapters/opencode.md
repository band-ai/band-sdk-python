# OpenCode Integration

`OpencodeAdapter` maps each Band room to an OpenCode session on a running
`opencode serve`: room messages become prompts, and the server's SSE stream is
relayed back as room messages, tool narration, and error events.

For the managed-host construction surface, see
[Managed Host Adapter Configuration](managed-host-adapters.md).

## OpenCode variants

OpenCode accepts an opaque, provider- and model-specific `variant` name on each
prompt. A variant can select reasoning effort when the chosen model exposes an
effort variant, but it can also be a custom provider setting. Check the running
OpenCode server's model catalog before selecting one; do not assume that a value
such as `"high"` is supported everywhere.

```python
from band.adapters.opencode import OpencodeAdapter, OpencodeAdapterConfig

adapter = OpencodeAdapter(
    config=OpencodeAdapterConfig(
        provider_id="openai",
        model_id="gpt-5",
        variant="high",
        custom_section="Keep updates concise.",
    )
)
```

Set `provider_id` and `model_id` together. The adapter omits a model override
unless both are present. OpenCode server credentials, server authentication, and
the variants available for a model are owned by the running OpenCode server, not
by `OpencodeAdapterConfig`. See the [OpenCode examples](../../examples/opencode/README.md)
for server setup and model discovery.

| Purpose | Path |
|---|---|
| Adapter package | `src/band/adapters/opencode/{adapter,approvals,config}.py` |
| Typed SSE events | `src/band/integrations/opencode/events.py` |
| HTTP/SSE client | `src/band/integrations/opencode/client.py` |

Four invariants are easy to break and expensive to rediscover:

- **Band tools are never gated.** A `permission.asked` naming one of the
  adapter's own registered tools is auto-approved with `always` in *every*
  `approval_mode` (codex parity — it runs band tools with no gate). Only
  non-tool asks, such as OpenCode's `doom_loop` heuristic, follow the mode, so a
  headless room with no approver should run `approval_mode="auto_accept"`.
- **OpenCode prefixes MCP tools with the server name** (`band_store_memory`
  surfaces as `<server>_band_store_memory`). Reported `tool_call`/`tool_result`
  names are canonicalized back, so consumers match one vocabulary.
- **One `serve` is shared by every agent on the host**, and it keys MCP
  registrations globally by name. Each agent registers under a name derived from
  its Band identity, and every prompt scopes tool visibility to that
  registration (deny the shared namespace, then re-allow its own — OpenCode
  applies the last matching rule).
- **The model is told the current `chat_id` every turn.** The band MCP tools'
  schemas require it, so without the per-turn Room Context block the platform
  tools are uncallable.

`turn_timeout_s` bounds *compute*: time parked on a manual approval is excluded,
since the ask carries its own `approval_wait_timeout_s` expiry.
