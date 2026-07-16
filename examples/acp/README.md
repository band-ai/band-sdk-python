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
| `generic.py` | Generic/Codex ACP client (command from env) |
| `rich_streaming.py` | Rich streaming of tool calls / plans / text |
| `cursor.py` | Cursor CLI with a vendor profile + auth |
| `bridge_architecture.py` | Fully env-driven bridge configuration |
| `copilot.py` | GitHub Copilot CLI (`copilot --acp`), stdio or TCP |

## `copilot_docker/` — Copilot-in-a-container deployments

Copilot runs in a container; the Band SDK connects over **TCP**, and Band tools are
served by a `band-mcp` (SSE) server. See each subfolder's README.

| Folder | Topology |
|--------|----------|
| `compose/` | Multi-service: `copilot` + `band-mcp` on one compose network |
| `colocated/` | Single container running both `copilot` and `band-mcp` |

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

`setup_logging.py` is a shared helper for the `servers/` and `clients/` examples.
The `copilot_docker/*` clients are self-contained (no shared helper) so they copy
cleanly into a deployment.
