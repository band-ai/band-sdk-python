# Codex Adapter

`CodexAdapter` runs one Codex process per Band room over stdio, each in its own
workspace. Runnable scripts: [examples/codex/](../../examples/codex/).

- **Where settings go.** Runtime settings live in `CodexAdapterConfig`, which
  forbids unknown fields, so a misplaced `emit=` or a typo fails construction.
  `emit=`, `capabilities=` and `additional_tools=` go on `CodexAdapter`.
- **Room approvals need a non-default `approval_policy`.** It is forwarded to
  Codex unchanged, and the default `"never"` means Codex never asks.
  `approval_mode` only decides how Band answers the requests Codex does send.
  `"on-request"` with `"read-only"` does not guarantee a request: Codex may
  attempt the command inside the sandbox and receive a denial without asking.
  The model may need to request escalation explicitly. For commands that need
  approval without model-requested escalation, the app-server RPC supports
  `"untrusted"` (verified with CLI 0.149.0 and 0.160.0). Commands must use
  default sandbox permissions: Codex rejects explicit `require_escalated`
  overrides under this policy before requesting approval. The model must still
  attempt the tool call; the policy cannot force it to act. This is distinct from
  the [retired user/project TOML setting](https://learn.chatgpt.com/docs/agent-approvals-security);
  see the [released RPC schema](https://github.com/openai/codex/blob/rust-v0.149.0/codex-rs/app-server-protocol/schema/typescript/v2/AskForApproval.ts).
- **`sandbox_policy` pins the sandbox.** While it is set, the `/sandbox` room
  command is refused.
- **One workspace per room.** The default is `./.band-workspaces/<room-id>`, and
  a custom `workspace_for_room` must return a distinct absolute path for every
  live room. `cwd`, a non-stdio `transport` and a custom `client_factory` are
  rejected at construction because they cannot guarantee per-room isolation.
- **Codex's final text is a fallback reply.** With `fallback_send_agent_text`
  (on by default) it is posted when the turn did not reply through a Band tool.
- **`reasoning_effort` is not validated.** The valid values depend on the model
  and the Codex CLI version, and the backend rejects unknown ones.
- **`skill_roots` need Codex CLI 0.136.0 or newer.** Codex has no config key
  for extra skill folders, so the adapter sends them to each room's app-server
  with `skills/extraRoots/set` before the room's thread starts. An older CLI
  rejects the request, and the room fails to start rather than running without
  the skills. Roots must be absolute paths; Codex accepts ones that don't exist.
