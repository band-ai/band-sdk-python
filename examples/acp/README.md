# ACP examples

Examples for the SDK's ACP (Agent Client Protocol) integration, grouped by direction.

## `servers/` — editor → Band

An editor (Zed, Cursor, JetBrains, Neovim) connects to Band as a custom ACP agent.

| File | What it shows |
|------|---------------|
| `basic.py` | Minimal `ACPServer` bridging an editor to Band peers |
| `routing.py` | Server with slash-command / mode-based `AgentRouter` |
| `push_notifications.py` | Unsolicited `session_update` notifications to the editor |
| `jetbrains.py` | JetBrains-specific server setup |

## `clients/` — Band → remote ACP agent

Band drives a remote ACP agent (Codex, Cursor, Claude Code, GitHub Copilot, …) as a
backend via `ACPClientAdapter`.

| File | What it shows |
|------|---------------|
| `generic.py` | Generic/Codex ACP client with advertised model/reasoning selection |
| `rich_streaming.py` | Rich streaming of tool calls / plans / text |
| `cursor.py` | Cursor CLI backend with dynamic ACP configuration and room decisions |
| `bridge_architecture.py` | Fully env-driven bridge configuration |
| `copilot.py` | GitHub Copilot CLI (`copilot --acp`) over stdio |
| `antigravity.py` | Google's standalone Antigravity ACP server with private native room state |
| `antigravity-tools.py` | Application-defined SHA256 tool, shared task board, and visible tool events |
| `antigravity-memory.py` | Durable Band memory that can be recalled from another room |

## `copilot_docker/` — Copilot-in-a-container deployments

The Band SDK starts one Copilot ACP process per room inside Docker and speaks ACP over
its **stdio**; Band tools are served by a `band-mcp` (SSE) server inside Docker.

| Folder | Topology |
|--------|----------|
| `compose/` | Multi-service: `copilot` + `band-mcp` services; one `docker compose exec -T` per room |
| `colocated/` | One image with `copilot` + `band-mcp`; one `docker run -i --rm` container per room |

## `copilot_sandbox/` — Copilot in a Docker sandbox (sbx), over stdio

Copilot runs in an isolated Docker **microVM sandbox** ([`sbx`](https://docs.docker.com/ai/sandboxes/));
the SDK drives it over `sbx exec -i <sandbox> copilot --acp` (ordinary **stdio** — no
TCP/socat). Adds microVM isolation + a host-side secret proxy (the token never enters
the VM) + an auditable egress firewall. Includes an optional Docker sandbox kit that
starts `band-mcp` inside the sandbox for Band tools. See its README.

## Running

Each `.py` example is a standalone PEP 723 script:

```bash
uv run examples/acp/clients/copilot.py
```

## Antigravity

Run `clients/antigravity.py` from the repository root. Its PEP 723 source override
installs this checkout's `band-sdk[acp]`, so it also works before a wheel containing
the adapter is published. It uses `Agent.from_config` and the standard async agent
lifecycle; no separate Band MCP process is required.

### Install the native server

Use the **standalone ACP server**, tested with version 1.4.3. The ordinary `agy`
CLI is a different entry point. Select the archive for your OS and architecture
from the [official ACP registry entry](https://github.com/agentclientprotocol/registry/blob/main/antigravity-acp/agent.json),
download its Google-hosted archive, and verify the listed SHA256 before extracting.

Keep the **complete extracted distribution** together and add its directory to
`PATH`. The server needs its companion harness; exposing only a symlink to the
server can pass initialization/authentication and still fail session creation.
Preserve executable permissions on macOS/Linux.

| Host | Default command selected by the SDK |
|------|-------------------------------------|
| macOS | `agy_acp_server.par` |
| Linux | `agy_acp_server.par --uid=` |
| Windows | `agy_acp_server.exe` |

For example, after extraction on macOS/Linux:

```bash
export PATH="$HOME/.local/share/antigravity-acp:$PATH"
```

On PowerShell, add the actual extraction directory:

```powershell
$env:PATH = "$HOME\antigravity-acp;$env:PATH"
```

### Configure and run

Create a remote agent in Band and put its own ID/key in the gitignored
`agent_config.yaml` at the repository root:

```yaml
antigravity_agent:
  agent_id: "your-remote-agent-id"
  api_key: "your-agent-api-key"
```

Put `GEMINI_API_KEY` in the repository-root `.env.test`, or use `GOOGLE_API_KEY`
as a fallback. These are provider credentials, separate from the Band agent key.
When both are set, `GEMINI_API_KEY` wins. The example loads `.env.test` without
overwriting existing process variables, and explicitly passes the selected key
to the native server. No browser login is needed for this API-key path.

```bash
uv run examples/acp/clients/antigravity.py
```

Add the agent to a Band room and mention it, for example:
`@YourAgent Please introduce yourself in one sentence.` Stop with `Ctrl-C`.
The SDK owns the native processes and embedded authenticated Band MCP server.

Optional settings in `.env.test`:

| Setting | Meaning / default |
|---------|-------------------|
| `ANTIGRAVITY_COMMAND` | JSON argv array; defaults to the host command above |
| `ACP_AGENT_CWD` | Root for the SDK's per-room workspaces; otherwise the SDK default |
| `ANTIGRAVITY_HOME` | Native-state root; defaults to `~/.band/antigravity-acp` |
| `ANTIGRAVITY_MODEL` | Model ID advertised by the session; unset uses the native default |
| `ANTIGRAVITY_REASONING_EFFORT` | Advertised thinking value, such as `low`; unset uses the native default |
| `ANTIGRAVITY_APPROVAL_MODE` | `auto_accept` (SDK default) or `auto_decline` |

Commands are argv, not shell strings. For a custom Linux path, include `--uid=`:

```dotenv
ANTIGRAVITY_COMMAND='["/home/me/Antigravity ACP/agy_acp_server.par", "--uid="]'
ANTIGRAVITY_REASONING_EFFORT=low
```

On Windows, use a JSON array too; forward slashes avoid JSON backslash escaping:

```dotenv
ANTIGRAVITY_COMMAND='["C:/Users/me/Antigravity ACP/agy_acp_server.exe"]'
```

Model/thinking overrides must appear in the server's advertised catalog; an
unsupported value fails the turn visibly. The SDK reapplies configured selections
after loading a native session.

### More features

All three scripts share the credential and native settings above. Give each
concurrently running example its own Band agent identity and config entry:
`antigravity_agent`, `antigravity_tools_agent`, or `antigravity_memory_agent`.
Stop the current process before restarting the same identity.

**Application tools and task boards:**

```bash
uv run examples/acp/clients/antigravity-tools.py
```

Try: `Use digest to hash 'hello', create a Band task containing the digest,
mark your work completed, then read its details and history.` The custom tool
executes in the SDK process through the embedded MCP server. Task tools update
the shared Band room board; native Antigravity tasks are separate. Tool events
show the real calls in the room. Task support is opt-in in this example.

**Durable memory:**

```bash
uv run examples/acp/clients/antigravity-memory.py
```

Try: `Remember in Band memory that I prefer concise status updates.` Then add the
same agent to a different room and ask: `What do you remember about my update
style? Search Band memory.` This checks persistent platform memory without
relying on the earlier room's native conversation. Memory belongs to that agent;
a second example identity does not inherit it.

**Permissions and room isolation:** with the basic example, set
`ANTIGRAVITY_APPROVAL_MODE=auto_decline` and request a shell command that writes a
temporary workspace file. Requested shell execution should be refused; Band
messaging and participant lookup remain available. Workspace reads and scheduling
have the native limitations below. To check isolation, give the same agent a
different conversation note in two rooms and ask for each note independently.
For collaboration, add two separately configured examples to one room and ask one
to mention the other through Band messaging. For restart recall, stop and relaunch
the same identity with the same home/workspace settings before asking for its note.

### State and permission boundaries

Each agent/room has a private native directory below `ANTIGRAVITY_HOME`.
Stopping the agent keeps that state for native resume. Keep the same agent identity,
native root and workspace configuration across restarts. The SDK resumes a recorded
session when possible and otherwise falls back to Band transcript history; a failed
or refused initial seed does not produce a resume record. Band memory is separate
from native Antigravity state.

Native databases contain MCP credentials. The SDK makes its home directories
private on POSIX; a custom Windows root needs appropriate user ACLs. Configure the
root through `ANTIGRAVITY_HOME`, not a child `GEMINI_HOME` environment override.

`auto_accept` grants requested native/external tools once, including shell, file,
browser and web access. `auto_decline` rejects those permission requests while
keeping Band tools available during the active room turn. Requested native
subagent calls are declined in both modes. **Scheduling and some workspace reads
bypass ACP permissions**, so neither mode is a sandbox or a blanket native-tool
block. Safe idle retention is not enabled. Native `send_message`/inbox operations
do not deliver Band replies; the adapter teaches the model to use Band messaging.

Antigravity may report a tool-result label rather than raw output in live ACP
narration. That does not mean the model received only the label. This example
does not enable on-prem-only file tools or automatic contact approval.

### Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| Executable not found | The extracted server directory is absent from `PATH` | Add the complete distribution directory, or set JSON `ANTIGRAVITY_COMMAND` |
| Initialization succeeds but session creation reports a missing harness | Only the server was copied/symlinked | Restore its companion files in the original extracted layout |
| Missing-key error before startup | Neither provider key is set | Set `GEMINI_API_KEY` or `GOOGLE_API_KEY` in `.env.test` |
| Provider rejects a session or reports quota/rate errors | Invalid key, account/model availability or quota | Check the provider account and use an advertised available model |
| Unknown model/thinking selection | The requested value is absent from the catalog | Remove the override or select an advertised value |
| Room stays quiet | Missing room membership/mention, native-only messaging, or an unfinished native prompt | Check membership and mention the agent; inspect Band failure/turn status before retrying |
| Recall is lost after restart | Changed identity/home/workspace, missing native session or unavailable transcript | Restore the original configuration and inspect the recorded resume task/fallback history |
