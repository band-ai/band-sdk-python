# OpenCode Integration

`OpencodeAdapter` maps each Band room to an OpenCode session on a running
`opencode serve`: room messages become prompts, and the server's SSE stream is
relayed back as room messages, tool narration, and error events.

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
  applies the last matching rule). If the adapter's Band MCP server dies, the
  next turn replaces it on a new port and re-registers it under the same name,
  which OpenCode treats as a replacement and reconnects.
- **The model is told the current `chat_id` every turn.** The band MCP tools'
  schemas require it, so without the per-turn Room Context block the platform
  tools are uncallable.

`turn_timeout_s` bounds *compute*: time parked on a manual approval is excluded,
since the ask carries its own `approval_wait_timeout_s` expiry.

`variant` is an opaque, provider- and model-specific name the running server
owns: it may select reasoning effort or be a custom provider setting, so read
the server's model catalog rather than assuming `"high"` exists. The model
override is sent only when `provider_id` and `model_id` are both set.
