# band-mcp Examples

`band-mcp` (`packages/band-mcp`) is a standalone, published MCP server —
you don't need `band-sdk` at all to use it. These examples drive the
**stdio** transport (the IDE-integration path: Cursor, Claude Desktop, Claude
Code) and each demonstrates a genuinely different capability, not the same
task through different clients:

| Example | Consumer | Scope / tools | What it proves |
|---|---|---|---|
| `01_raw_client.py` | plain `mcp` python SDK | `--scope agent` | **Dynamic room composition** — create a room, discover a peer with `band_lookup_peers`, pull them in with `band_add_participant`, message them. No LLM, no framework — the wire contract itself. |
| `02_claude_agent_sdk_external.py` | vanilla `claude_agent_sdk` | `--scope agent --tools memory` | **Durable memory across processes** — one Claude session stores a fact via `band_store_memory`; a second, fully independent session (fresh `band-mcp` subprocess, no shared Python state) recalls it via `band_list_memories`. Contrast with `ClaudeSDKAdapter`, which hands Claude an in-process `LocalMCPServer` instead of spawning `band-mcp` as an external process. |
| `03_langgraph_external.py` | LangGraph + `langchain-mcp-adapters` | `--scope human` | **Human-scope personal assistant** — a person's own `BAND_USER_KEY`, no agent identity involved at all: list *my* chats, read the most recent one, summarize it. Shows band-mcp's dual-scope design and works as a generic tool source for a framework with no Band-specific relationship. |

Each script is self-contained (PEP 723 inline metadata) and runs standalone
with `uv run` — it installs `band-mcp` into its own ephemeral environment, so
the `band-mcp` command it spawns as a subprocess is on `PATH` for the
duration of the run. No separate `pip install band-mcp` step needed.

## Prerequisites

- **Examples 01 and 02** (agent scope): an agent-scoped Band API key —
  `BAND_AGENT_KEY`, starts with `band_a_`.
- **Example 03** (human scope): a user-scoped Band API key — `BAND_USER_KEY`,
  starts with `band_u_`.
- Create either at
  [app.band.ai/settings/api-keys](https://app.band.ai/settings/api-keys).
- Examples 02 and 03 also need `ANTHROPIC_API_KEY`.

```bash
export BAND_AGENT_KEY="band_a_..."  # examples 01, 02
export BAND_USER_KEY="band_u_..."   # example 03
export ANTHROPIC_API_KEY="sk-ant-..."  # examples 02, 03
```

Examples 01 and 03 provision (or list) their own rooms — 01 first spawns an
unpinned `band-mcp` process and calls `band_create_chatroom` (not
room-bound — no `chat_id` needed), then spawns the "real" session pinned to
that fresh room id via `--room-id`. No pre-existing room or manual setup
step required for any of the three.

## Running

```bash
uv run examples/band_mcp/01_raw_client.py
uv run examples/band_mcp/02_claude_agent_sdk_external.py  # needs Node.js 20+ and @anthropic-ai/claude-code
uv run examples/band_mcp/03_langgraph_external.py
```

## Troubleshooting

### `band-mcp: command not found` after edits to `packages/band-mcp`

`uv run`'s inline-script dependency (`dependencies = ["band-mcp"]`) resolves
against PyPI by default, not your local checkout. To exercise local changes,
run from the repo root with the workspace member instead:

```bash
uv run --package band-mcp band-mcp --scope agent
```

and point one of the example scripts at that already-running process instead
of letting it spawn its own (or temporarily edit the script's `command` to an
absolute path, e.g. `.venv/bin/band-mcp`).

### `ConfigError: agent scope requested but no agent credential available`

`BAND_AGENT_KEY` is unset or empty (examples 01/02). For example 03's
`ConfigError: human scope requested but no user credential available`,
it's `BAND_USER_KEY` instead. Both match band-mcp's own CLI-flag/env
precedence (CLI flag > env).

### Example 02 hangs or the CLI can't find `claude`

Install the Claude Code CLI the Agent SDK shells out to:

```bash
npm install -g @anthropic-ai/claude-code
```

See `examples/claude_sdk/README.md` for the full Node.js/Docker setup this
example shares.
