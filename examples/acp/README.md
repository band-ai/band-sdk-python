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

Band drives a remote ACP agent (Codex, Cursor, Claude Code, GitHub Copilot, OMP, …) as a
backend via `ACPClientAdapter`.

| File | What it shows |
|------|---------------|
| `generic.py` | Generic/Codex ACP client with advertised model/reasoning selection |
| `rich_streaming.py` | Rich streaming of tool calls / plans / text |
| `cursor.py` | Cursor CLI with a vendor profile + auth |
| `bridge_architecture.py` | Fully env-driven bridge configuration |
| `copilot.py` | GitHub Copilot CLI (`copilot --acp`), stdio or TCP |
| `omp.py` | Oh My P.I. (`omp acp`), stdio with OpenAI API auth |

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

### OMP

OMP's native ACP server runs over stdio. Install Bun (version 1.3.14 or newer),
then install `@oh-my-pi/pi-coding-agent` so `omp` is on `PATH`. Set
`OPENAI_API_KEY`; `omp.py` passes it to the child process with the
`openai/gpt-5.4-mini` model. For automated runs, use a fresh
`PI_CODING_AGENT_DIR` and disposable working directory. Keep OMP in a
permission-gated approval mode such as `always-ask`: `--yolo` / auto-approve modes
bypass the resolver that protects tool calls.
