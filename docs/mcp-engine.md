# MCP Engine

One MCP-framework-neutral engine (`src/band/integrations/mcp/engine.py`)
builds every Band MCP tool registration; two front doors consume it instead
of each hand-rolling their own FastMCP/lowlevel-Server wiring:

| Front door | Module | Runs |
|---|---|---|
| Published CLI | `packages/band-mcp` (`band_mcp.server`, `band_mcp.shared`) | Standalone `band-mcp` process, stdio or SSE, against a real Band room over REST |
| Embedded server | `src/band/integrations/mcp/local_server.py` (`LocalMCPServer`) | In-process, for adapters that need to hand an already-live `AgentTools` to an external agent (OpenCode, the desktop app, ACP client sessions) |

`EngineSpec`/`MCPToolRegistration` describe *what* to register (name,
description, Pydantic input model, an async `execute`); `build_engine(spec)`
turns that into a real `FastMCP` instance. Each front door supplies its own
`ToolsResolver` (a single `invoke(definition, chat_id, arguments)` method)
that decides *how* a call reaches Band:

- `EmbeddedResolver` (embedded door): no cache, resolves straight to a
  caller-supplied `AgentTools`/`AgentToolsProtocol`.
- `StandaloneResolver` (`band_mcp.shared`, CLI door): human-tools singleton
  dispatch plus an LRU-cached (128), lock-striped (64) per-room `AgentTools`
  pool, since one CLI process can serve many rooms over its lifetime.

The model-facing argument is always `chat_id`, never `room_id` — the
Python-side variable/field is still `room_id` throughout the codebase, only
the text the model sees (tool descriptions, schema field names, prompt
blocks like OpenCode's per-turn Room Context) says `chat_id`.

**MCP-package imports are confined to an explicit allowlist**
(`tests/mcp/test_import_boundary.py`): `engine.py`, `local_server.py`,
`desktop_app/server.py`, and `band_mcp/{shared,server}.py`. This is enforced
by an AST scan, not a convention — it exists so an MCP Python SDK major-
version migration only has to touch those five files, not audit the tree for
stray `mcp`-package imports. A new module that genuinely needs to import
`mcp` directly belongs on that allowlist with a comment saying why; anything
else should go through the engine or a resolver instead.

**The published CLI's wire contract is pinned by declarative code, not a
snapshot.** `tests/mcp/test_wire_contract.py` drives a real `list_tools()`
round trip and checks it against small, hand-written `ToolContract`
entries — only tools with a genuinely non-obvious wire invariant (an
enum, an array item type, a required-set) get one; enum values are read
from the real `StrEnum`/`Literal` they come from, never copied by hand.
`chat_id` room-binding (required when unpinned, hidden when pinned) is
checked once, generically, against `AGENT_ROOM_BOUND_TOOL_NAMES`.
