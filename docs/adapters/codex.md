# Codex Adapter

`CodexAdapter` runs one Codex process per Band room over stdio, each in its own
workspace. Runnable scripts: [examples/codex/](../../examples/codex/).

Automatic idle release is unavailable pending native safety qualification;
see [idle room resource retention](../idle-room-release.md).

For existing integrations, follow the [SDK 4.x to 5.x migration](../acp.md#upgrading-from-sdk-4x-to-5x).

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
- **Ownership survives recovery and failed cleanup.** The workspace stays
  claimed until the room leaves and its process is closed. Failed startup,
  close errors and close timeouts retain the claim for retry. A timed-out or
  cancelled caller does not abandon subprocess cleanup; retry room cleanup to
  finish releasing ownership. Shutdown attempts every room and reports failures.
- **Leaving is final.** Leaving a room stops it admitting work at once. A
  leave that fails or runs over keeps the process, thread and workspace claim,
  and the room refuses work until a retried leave succeeds; a later rejoin
  starts a new thread.
- **A lost process keeps its thread.** When a room's process exits or its
  transport fails, the turn fails and the process is closed once that turn
  ends. The next message starts a new process and resumes exactly the same
  thread, with its private context and saved tools. If Codex refuses that
  resume (for example "no rollout found" for a thread that never ran a turn),
  the turn fails and the thread is kept: the adapter never starts a different
  thread in its place. Send `/thread archive` to start a new thread instead.
- **Starting a process has its own budget.** Starting a room's process,
  refreshing `model/list` and resuming its thread must finish within
  `retention.transition_timeout_s`, which Codex defaults to
  `CODEX_TRANSITION_TIMEOUT_S` (60 s). A start that runs over fails the turn
  with `PROCESS_NOT_READY_ERROR` and is cancelled and closed; raise the bound
  for slow starts.
- **Native work answers to the turn that started it.** Each tool call or
  approval request is handled for the Band turn whose native turn sent it, on
  the process that sent it. Requests from a sub-agent the turn delegated to
  belong to that turn once one of its items names the sub-agent's thread: a
  collab tool call, or `multi_agent_v2`'s sub-agent activity.
  A request from a native turn no Band turn started gets a failed tool result
  or a declined approval, and late events of a finished turn are dropped
  rather than reaching the next turn.
- **Band tools own replies and declines.** Native completed text is optional
  thought telemetry without mentions; it never settles a turn. A native-only
  turn fails the shared missing-reply verdict. `Emit.THOUGHTS` controls visibility.
  Remove retired `fallback_send_agent_text` and `assistant_text_mode` config keys
  and `CODEX_FALLBACK_SEND_AGENT_TEXT` / `CODEX_ASSISTANT_TEXT_MODE` environment
  variables. Explicit configs reject retired keys; the environment keys are ignored.
- **Tool filters apply to fresh threads.** Include/category/exclude filters narrow
  the dynamic platform tools advertised when starting a thread. Resumed threads
  retain their saved registrations: start a fresh thread after changing filters.
  After a lost process, a turn whose tools differ from the ones its restored
  thread was started with is refused, since Codex would silently ignore them.
  First-prompt context refreshes communication instructions, not saved tool schemas.
- **`reasoning_effort` is not validated.** The valid values depend on the model
  and the Codex CLI version, and the backend rejects unknown ones.
- **`skill_roots` need Codex CLI 0.136.0 or newer.** Codex has no config key
  for extra skill folders, so the adapter sends them to each room's app-server
  with `skills/extraRoots/set` before the room's thread starts. An older CLI
  rejects the request, and the room fails to start rather than running without
  the skills. Roots must be absolute paths; Codex accepts ones that don't exist.

```python
from band.adapters.codex import (
    CODEX_TRANSITION_TIMEOUT_S,
    PROCESS_NOT_READY_ERROR,
    CodexAdapterConfig,
)

assert (
    CodexAdapterConfig().retention.transition_timeout_s
    == CODEX_TRANSITION_TIMEOUT_S
    == 60
)
assert PROCESS_NOT_READY_ERROR == "Codex process did not become ready in time"
```

While an accepted turn awaits approval, newer ordinary room work remains
outstanding. The runtime can deliver approval and status controls ahead of that
pending work through Codex's existing authorization rules. The active turn keeps
its original tools; an ordinary input rejected before provider invocation is
retried by the shared room scheduler after that turn releases the session.
