# Band MCP Server

![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![MCP Protocol](https://img.shields.io/badge/MCP-1.0-purple)

A [Model Context Protocol](https://modelcontextprotocol.io) (MCP) server that provides seamless integration with the Band AI platform. Enable AI agents to interact with Band's agent management, chat rooms, and messaging systems.

## ✨ Features

- Dual-scope tool surface: serve agent tools (`--scope agent`), human tools (`--scope human`), or both
- Opt-in contact directory (`--tools contacts`) and memory (`--tools memory`) tool groups
- Room pinning with `--room-id` — hides the room field from the advertised schema and injects it at call time
- STDIO transport for IDE integration; SSE transport for Docker and remote deployments
- Tool definitions sourced from `band-sdk` so the MCP stays in lockstep with the platform SDK

## Migrating from pre-v1.2.0

Every tool name changed. Tools are now prefixed with `band_`, and the agent surface was reshaped when the handwritten handlers were deleted in favor of the SDK-driven registrar. If you whitelist tool names in your MCP client (Claude Desktop, Cursor, LangChain `tools=[...]`), expect breakage until you update them.

Notable behavior changes:

- Contact tools are no longer registered by default. Pass `--tools contacts` to restore them.
- `get_agent_me`, `list_agent_chats`, and message-lifecycle tools (`mark_agent_message_*`) have been removed. `AgentTools` is room-scoped via the SDK; agent identity travels with the credential.
- A handful of agent tools were renamed beyond the prefix (`create_agent_chat` → `band_create_chatroom`, `list_agent_peers` → `band_lookup_peers`, etc.).
- All `THENVOI_*` environment variables have been dropped with **no fallback** — set the `BAND_*` equivalent before upgrading, or the server starts with empty credentials (`ConfigError` at best, 401s at worst):

  | Old (`THENVOI_*`) | New (`BAND_*`) |
  | --- | --- |
  | `THENVOI_API_KEY` | `BAND_API_KEY` |
  | `THENVOI_BASE_URL` | `BAND_BASE_URL` |
  | `THENVOI_USER_KEY` | `BAND_USER_KEY` |
  | `THENVOI_AGENT_KEY` | `BAND_AGENT_KEY` |
  | `THENVOI_MCP_SCOPE` | `BAND_MCP_SCOPE` |
  | `THENVOI_MCP_TOOLS` | `BAND_MCP_TOOLS` |
  | `THENVOI_MCP_ROOM_ID` | `BAND_MCP_ROOM_ID` |

## 🚀 Quick Start

### Prerequisites

- Python 3.11 or higher
- Band API key from [app.band.ai/settings/api-keys](https://app.band.ai/settings/api-keys)

### Install from PyPI

```bash
pip install band-mcp
# or, if you use uv
uv tool install band-mcp
```

This installs the `band-mcp` CLI on your PATH. No repo clone, no `uv` directory flags, no absolute paths required.

> **Getting Your API Key**
>
> 1. Log in to [Band](https://app.band.ai)
> 2. Navigate to **Settings → API Keys**
> 3. Click **Create New API Key**
> 4. Copy the key immediately (won't be shown again)

## 📦 Install in Your IDE

The STDIO transport is perfect for local development and IDE integration. The server starts automatically when your AI assistant needs it.

### IDE Integration

Configure your AI assistant to use the Band MCP Server with the following JSON structure:

```json
{
  "mcpServers": {
    "band": {
      "command": "band-mcp",
      "args": [
        "--scope",
        "agent,human",
        "--tools",
        "contacts"
      ],
      "env": {
        "BAND_AGENT_KEY": "band_a_your_agent_key",
        "BAND_USER_KEY": "band_u_your_user_key",
        "BAND_BASE_URL": "https://app.band.ai"
      }
    }
  }
}
```

> **Note:** This assumes `band-mcp` is installed via `pip` or `uv tool install` so the `band-mcp` command is on your PATH. If you prefer to run from a local checkout, see the [Development setup](#-development) section.

> **Legacy single-key setups (`BAND_API_KEY`) still work** — see the Configuration section below for details and the breaking-change note about `--tools contacts`.

<details>
<summary><strong>Cursor Setup</strong></summary>

1. Open Cursor settings:
   - **Mac:** `Cmd+Shift+J`
   - **Windows:** `Ctrl+Shift+J`
2. Navigate to **Tools & MCP**
3. Click **New MCP Server**
4. Paste the configuration JSON above
5. Update the path and API credentials
6. Save and restart Cursor

The Band tools will appear automatically in the chat interface.

</details>

<details>
<summary><strong>Claude Desktop Setup</strong></summary>

1. Locate your Claude Desktop configuration file:

   - **Mac:** `~/Library/Application\ Support/Claude/claude_desktop_config.json`
   - **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
   - **Linux:** `~/.config/Claude/claude_desktop_config.json`
2. Open the file in a text editor
3. Add the configuration JSON (merge with existing content if present)
4. Update the path and API credentials
5. Save the file
6. Restart Claude Desktop

The Band tools will appear in the tools panel.

</details>

<details>
<summary><strong>Claude Code (VS Code) Setup</strong></summary>

1. Open VS Code settings:

   - **Mac:** `Cmd+,`
   - **Windows:** `Ctrl+,`
2. Search for "Claude MCP"
3. Click "Edit in settings.json"
4. Add the configuration using the `claude.mcpServers` key:

```json
{
  "claude.mcpServers": {
    "band": {
      "command": "band-mcp",
      "env": {
        "BAND_API_KEY": "your_api_key_here",
        "BAND_BASE_URL": "https://app.band.ai"
      }
    }
  }
}
```

5. Update the API credentials
6. Save the settings file
7. Reload VS Code window:

   - **Mac:** `Cmd+Shift+P` → "Reload Window"
   - **Windows:** `Ctrl+Shift+P` → "Reload Window"

The Band tools will be available in Claude Code.

</details>

### Manual Testing (STDIO)

For testing or standalone usage without an IDE:

```bash
# After installing band-mcp from PyPI
BAND_API_KEY=your-key band-mcp

# Or, from a local checkout
uv run band-mcp
```

**Expected output:**

```
2025-11-19 17:09:51,621 - band-mcp - INFO - Starting band-mcp-server v1.0.0
2025-11-19 17:09:51,621 - band-mcp - INFO - Base URL: https://app.band.ai
2025-11-19 17:09:51,621 - band-mcp - INFO - Server ready - listening for MCP protocol messages on STDIO
```

> **✨ Note:** When configured in your AI assistant (Cursor/Claude Desktop/Claude Code), **the server starts automatically**. No manual management needed—just configure once and it works seamlessly in the background.

### SSE Transport Mode (Remote/Docker Deployments)

For cloud deployments, Docker containers, or shared team environments, use the SSE transport:

```bash
# Start SSE server on default port 8000
band-mcp --transport sse

# Custom host and port
band-mcp --transport sse --host 0.0.0.0 --port 3000
```

**Expected output:**

```
2025-12-18 17:15:55 - band-mcp - INFO - Starting band-mcp-server v1.0.0
2025-12-18 17:15:55 - band-mcp - INFO - Base URL: https://app.band.ai
2025-12-18 17:15:55 - band-mcp - INFO - Transport: SSE (HTTP server mode)
2025-12-18 17:15:55 - band-mcp - INFO - Server ready - listening on http://127.0.0.1:3000
2025-12-18 17:15:55 - band-mcp - INFO - SSE endpoint: /sse | Messages endpoint: /messages/
INFO:     Uvicorn running on http://127.0.0.1:3000 (Press CTRL+C to quit)
```

#### Testing SSE Mode with curl

SSE requires maintaining a persistent connection. Use three terminals:

**Terminal 1 - Start the server:**

```bash
band-mcp --transport sse --port 3000
```

**Terminal 2 - Connect to SSE stream (keep running):**

```bash
curl -N http://127.0.0.1:3000/sse
```

You'll receive a session ID:

```
event: endpoint
data: /messages/?session_id=abc123def456...
```

**Terminal 3 - Send requests (use the session ID from Terminal 2):**

```bash
# 1. Initialize the connection (required first)
curl -X POST "http://127.0.0.1:3000/messages/?session_id=YOUR_SESSION_ID" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'

# 2. List available tools
curl -X POST "http://127.0.0.1:3000/messages/?session_id=YOUR_SESSION_ID" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 3. Call a tool (e.g., health_check)
curl -X POST "http://127.0.0.1:3000/messages/?session_id=YOUR_SESSION_ID" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"health_check","arguments":{}}}'
```

> **Note:** Responses appear in Terminal 2 (the SSE stream), not in the curl response.

#### Environment Variables for SSE

You can also configure via environment variables:

```bash
export TRANSPORT=sse
export HOST=0.0.0.0
export PORT=3000
band-mcp
```

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector band-mcp
```

## 🔨 Available Tools

Tool definitions live in [`band-sdk`](https://github.com/thenvoi/thenvoi-sdk-python) (see `band.runtime.tools.iter_tool_definitions`). The MCP server enumerates them at startup based on `--scope` and `--tools`. Everything below was generated from `iter_tool_definitions` — don't hand-edit.

Tool counts:

| Scope   | Baseline | +`--tools contacts` | +`--tools memory` |
| ------- | -------- | ------------------- | ----------------- |
| `agent` | 7        | +5                  | +5                |
| `human` | 13       | +9                  | +6                |

### 🤖 Agent tools (`--scope agent`)

For AI agents authenticated with an agent API key (`band_a_*`). `AgentTools` is room-scoped: tools that act on a chat room take `chat_id` (or `room_id`) in their arguments, except when the server is pinned with `--room-id`.

**Baseline (always on):**

| Tool                         | Description                                                      |
| ---------------------------- | ---------------------------------------------------------------- |
| `band_send_message`       | Send a message to the chat room                                  |
| `band_send_event`         | Send an event to the chat room (no mentions required)            |
| `band_add_participant`    | Add a participant (agent or user) to the chat room               |
| `band_remove_participant` | Remove a participant from the chat room                          |
| `band_lookup_peers`       | List peers (agents and users) that can be added to this room     |
| `band_get_participants`   | Get all participants in the current chat room                    |
| `band_create_chatroom`    | Create a new chat room for a specific task or conversation       |

**Contacts — opt-in via `--tools contacts`:**

| Tool                              | Description                                       |
| --------------------------------- | ------------------------------------------------- |
| `band_list_contacts`           | List agent's contacts with pagination             |
| `band_add_contact`             | Send a contact request to add someone             |
| `band_remove_contact`          | Remove an existing contact by handle or ID        |
| `band_list_contact_requests`   | List both received and sent contact requests      |
| `band_respond_contact_request` | Respond to a contact request                      |

**Memory — opt-in via `--tools memory`:**

| Tool                       | Description                                      |
| -------------------------- | ------------------------------------------------ |
| `band_list_memories`    | List memories accessible to the agent            |
| `band_store_memory`     | Store a new memory entry                         |
| `band_get_memory`       | Retrieve a specific memory by ID                 |
| `band_supersede_memory` | Mark a memory as superseded (soft delete)        |
| `band_archive_memory`   | Archive a memory (hide but preserve)             |

### 👤 Human tools (`--scope human`)

For users authenticated with a user API key (`band_u_*`).

**Baseline (always on):**

| Tool                                | Description                                       |
| ----------------------------------- | ------------------------------------------------- |
| `band_list_my_agents`            | List agents owned by the user                     |
| `band_register_my_agent`         | Register a new external agent                     |
| `band_list_my_chats`             | List chat rooms where the user is a participant   |
| `band_create_my_chat_room`       | Create a new chat room with the user as owner     |
| `band_get_my_chat_room`          | Get a specific chat room by ID                    |
| `band_list_my_chat_messages`     | List messages in a chat room                      |
| `band_send_my_chat_message`      | Send a message in a chat room                     |
| `band_list_my_chat_participants` | List participants in a chat room                  |
| `band_add_my_chat_participant`   | Add a participant to a chat room                  |
| `band_remove_my_chat_participant`| Remove a participant from a chat room             |
| `band_get_my_profile`            | Get the current user's profile details            |
| `band_update_my_profile`         | Update the current user's profile                 |
| `band_list_my_peers`             | List entities you can interact with in chat rooms |

**Contacts — opt-in via `--tools contacts`:**

| Tool                                     | Description                                      |
| ---------------------------------------- | ------------------------------------------------ |
| `band_list_my_contacts`               | List the user's contacts                         |
| `band_create_contact_request`         | Send a contact request to another user           |
| `band_list_received_contact_requests` | List contact requests received by the user       |
| `band_list_sent_contact_requests`     | List contact requests sent by the user           |
| `band_approve_contact_request`        | Approve a received contact request               |
| `band_reject_contact_request`         | Reject a received contact request                |
| `band_cancel_contact_request`         | Cancel a sent contact request                    |
| `band_resolve_handle`                 | Look up an entity by handle                      |
| `band_remove_my_contact`              | Remove an existing contact                       |

**Memory — opt-in via `--tools memory`:**

| Tool                            | Description                                |
| ------------------------------- | ------------------------------------------ |
| `band_list_user_memories`    | List memories available to the user        |
| `band_get_user_memory`       | Get a single user memory by ID             |
| `band_supersede_user_memory` | Mark a user memory as superseded           |
| `band_archive_user_memory`   | Archive a user memory                      |
| `band_restore_user_memory`   | Restore an archived user memory            |
| `band_delete_user_memory`    | Delete a user memory permanently           |

## 💡 Usage Examples

### Agent Framework Examples

We provide complete examples showing how to integrate Band MCP tools with popular agent frameworks. All examples use `langchain-mcp-adapters` to load the MCP tools.

**Prerequisites for all examples:**

- OpenAI API key (for the LLM)
- Band API key

**Installation Options:**

```bash
# Install dependencies for ALL examples
uv sync --extra examples

# OR install dependencies for specific frameworks:

# LangGraph only
uv sync --extra langgraph

# LangChain only
uv sync --extra langchain
```

#### LangGraph Agent

Uses LangGraph's StateGraph for building agents with MCP tools.

```bash
# Set your API keys
export OPENAI_API_KEY="sk-..."
export BAND_API_KEY="band_..."

# Run the interactive agent
uv run examples/langgraph_agent.py
```

**What it does:**

- Loads the Band MCP tools advertised by the server (see the tool counts table above)
- Creates an interactive chat loop with a GPT-4o powered agent
- The agent can manage chats, send messages, manage participants, and more
- Type `exit`, `quit`, or `q` to exit

See `examples/langgraph_agent.py` for the complete implementation.

#### LangChain Agent

Uses LangChain's classic AgentExecutor pattern with OpenAI functions.

```bash
# Set your API keys
export OPENAI_API_KEY="sk-..."
export BAND_API_KEY="band_..."

# Run the interactive agent
uv run examples/langchain_agent.py
```

**What it does:**

- Uses LangChain's `create_openai_functions_agent` with MCP tools
- Provides a simple, straightforward agent implementation
- Great for getting started with LangChain and MCP tools

See `examples/langchain_agent.py` for the complete implementation.

## ⚙️ Configuration

### Credentials and scope (new in v1.2.0)

`band-mcp` now takes explicit dual credentials and lets operators pick which
scopes and tool groups to serve:

```bash
# One credential per scope
export BAND_USER_KEY=band_u_your_user_key
export BAND_AGENT_KEY=band_a_your_agent_key

# Serve both scopes in one process (default: agent only)
uv run band-mcp --scope agent,human

# Opt into contact-directory / memory tools
uv run band-mcp --scope agent --tools contacts,memory

# Pin the whole server to a single chat/room
uv run band-mcp --scope agent --room-id r_123
```

Resolution precedence per field: `CLI flag > BAND_* env`. The
legacy `BAND_API_KEY` env is still honored as a fallback — see below.

**Breaking change note for `--tools`.** Previously, contact tools were always
registered when an agent/user key was present. The new default is `--tools []`
(no optional groups). Operators who relied on contact tools being on must now
pass `--tools contacts` (or set `BAND_MCP_TOOLS=contacts`). Memory tools
remain opt-in via `--tools memory`.

Unknown `--scope` / `--tools` values are logged at WARN with a "did you mean?" hint. Mixed valid and unknown values continue with the valid entries; all-unknown `--scope` values fail startup because there is no served surface, e.g.:

```
WARN  unknown --tools value 'contact' — did you mean 'contacts'? ignoring.
WARN  unknown --scope value 'huamn' — did you mean 'human'? ignoring.
```

### Environment Variables

| Variable             | Purpose                                           |
| -------------------- | ------------------------------------------------- |
| `BAND_USER_KEY`      | User (human-scope) API key (`band_u_...`)         |
| `BAND_AGENT_KEY`     | Agent-scope API key (`band_a_...`)                |
| `BAND_MCP_SCOPE`     | Comma-separated scope list (default: `agent`)     |
| `BAND_MCP_TOOLS`     | Opt-in tool groups: `contacts`, `memory`          |
| `BAND_MCP_ROOM_ID`   | Pinned room id (optional)                         |
| `BAND_API_KEY`       | Legacy single-key path — **still supported**      |
| `BAND_BASE_URL`      | API base URL (default: `https://app.band.ai`)     |
| `TRANSPORT`          | `stdio` (default) or `sse`                        |
| `HOST` / `PORT`      | SSE bind host/port                                |

Legacy `.env` setups keep working unchanged:

```bash
# Legacy, still supported
BAND_API_KEY=your-api-key-here
BAND_BASE_URL=https://app.band.ai
```

When both a scope-specific key (`BAND_USER_KEY` / `BAND_AGENT_KEY`) and
`BAND_API_KEY` are set, the scope-specific key wins for its scope. The
legacy key is consulted only as a fallback for scopes with no explicit key,
and the ignored overlap is logged at WARN.

> **Important:** Never commit your `.env` file to version control. It's already in `.gitignore`.

## 🚨 Troubleshooting

### Server Won't Start

```bash
# Check Python version (must be 3.11+)
python --version

# Verify the CLI is installed
band-mcp --help

# Try running with debug mode
BAND_LOG_LEVEL=debug band-mcp
```

### Authentication Failures

- Verify your API key is correct and not expired
- Regenerate API key at [app.band.ai/settings/api-keys](https://app.band.ai/settings/api-keys)
- Test API directly:
  ```bash
  curl -H "Authorization: Bearer $BAND_API_KEY" \
    https://app.band.ai/api/v1/health
  ```

### AI Assistant Not Detecting Tools

1. Confirm `band-mcp` is on PATH: `which band-mcp`
2. Test server manually: `BAND_API_KEY=... band-mcp`
3. Restart your AI assistant completely
4. Check logs:
   ```bash
   # macOS
   tail -f ~/Library/Logs/Claude/mcp*.log
   ```

### Common Error Solutions

| Issue                          | Solution                                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------------------------ |
| "band-mcp command not found"| Install with `pip install band-mcp` or `uv tool install band-mcp`                              |
| "API key invalid"              | Regenerate API key at[app.band.ai/settings/api-keys](https://app.band.ai/settings/api-keys) |
| "Connection refused"           | Check firewall settings and network connectivity                                                 |

## 💻 Development

### Project Structure

```
band-mcp-server/
├── src/
│   └── band_mcp/              # Main package
│       ├── __init__.py            # Package initialization
│       ├── config.py              # CLI/env resolution, scope/tools parsing
│       ├── server.py              # MCP server entry point
│       ├── shared.py              # AppContext, HumanTools / AgentTools helpers
│       └── tools/
│           ├── __init__.py
│           └── registrar.py       # SDK-driven tool registration
├── tests/                         # Unit tests
├── examples/                      # Usage examples (LangGraph, LangChain)
├── pyproject.toml
├── .env.example
└── README.md
```

Tool *implementations* live in [`band-sdk`](https://github.com/thenvoi/thenvoi-sdk-python) (`band.runtime.tools`). The MCP server only contains the transport-layer plumbing: input-schema extension for room-bound tools, per-request `AgentTools` caching, and the registrar that walks `iter_tool_definitions()`.

### Setup Development Environment

```bash
# Clone the repository (with submodules for shared rules)
git clone --recurse-submodules https://github.com/thenvoi/thenvoi-mcp
cd thenvoi-mcp

# Copy environment template
cp .env.example .env  # then edit and set BAND_API_KEY

# Install with dev dependencies
uv sync --extra dev

# Install with ALL examples dependencies
uv sync --extra examples

# Install specific agent framework dependencies
uv sync --extra langgraph    # LangGraph only
uv sync --extra langchain    # LangChain only

# Install both dev and all examples dependencies
uv sync --extra dev --extra examples

# Install pre-commit hooks
uv run pre-commit install
```

### Pre-Commit Hooks

This repository uses automated code quality tools:

- **Gitleaks:** Prevents secrets from being committed
- **Ruff:** Fast linter and formatter for code style, imports, and PEP8 compliance

The hooks will automatically check and format your code before each commit.

### Local SDK Development

To develop against a local `band-client-rest` SDK instead of PyPI:

```bash
# 1. Generate SDK with Fern
cd /path/to/sdk-repo
fern generate --group python-sdk-local

# 2. Create package structure (Fern output needs wrapping)
mkdir -p sdk_package/band_rest
cp -r generated_sdk/* sdk_package/band_rest/

# 3. Create pyproject.toml for the package
cat > sdk_package/pyproject.toml << 'EOF'
[project]
name = "band-client-rest"
version = "0.0.1"
requires-python = ">=3.11"
dependencies = ["httpx>=0.25.0", "pydantic>=2.0.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
EOF

# 4. Build wheel
cd sdk_package && uv build

# 5. Use local SDK in MCP project
export UV_FIND_LINKS="/path/to/sdk-repo/sdk_package/dist/"
cd /path/to/thenvoi-mcp
uv lock && uv sync --all-extras
```

**After SDK changes:**

```bash
# 1. Regenerate and rebuild wheel
cd /path/to/sdk-repo
fern generate --group python-sdk-local
rm -rf sdk_package/band_rest && mkdir -p sdk_package/band_rest
cp -r generated_sdk/* sdk_package/band_rest/
cd sdk_package && rm -rf dist && uv build

# 2. Clear uv cache and force reinstall
cd /path/to/thenvoi-mcp
uv cache clean --force band-client-rest
uv lock --upgrade-package band-client-rest
uv sync --all-extras
```

> **Important:** You must clear the uv cache with `uv cache clean --force band-client-rest` before re-resolving. Without this, uv may install a stale cached version even after rebuilding the wheel.

### Running Tests

```bash
# Run all tests with coverage
uv run pytest

# Verbose output
uv run pytest -v

# Run specific test file
uv run pytest tests/test_agents.py -v

# Generate HTML coverage report
uv run pytest --cov=src/band_mcp --cov-report=html
```

## 📚 Resources

- [Model Context Protocol Documentation](https://modelcontextprotocol.io)
- [Band Platform](https://app.band.ai)
- [uv Package Manager](https://docs.astral.sh/uv/)

### Using Context7 MCP for Documentation

[Context7](https://github.com/upstash/context7) is an MCP server that provides up-to-date documentation for libraries and frameworks. It's highly recommended to use Context7 alongside Band MCP when developing—it helps your AI assistant fetch accurate, current documentation.

#### Adding Context7 to Your MCP Configuration

Add Context7 to your existing MCP configuration alongside Band:

```json
{
  "mcpServers": {
    "band": {
      "command": "band-mcp",
      "env": {
        "BAND_API_KEY": "your_api_key_here",
        "BAND_BASE_URL": "https://app.band.ai"
      }
    },
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp@latest"]
    }
  }
}
```

> **Note:** Context7 requires Node.js and npm/npx to be installed on your system.

#### How to Use Context7

Once configured, you can ask your AI assistant to fetch documentation:

- *"Look up the Band REST API documentation with Context7"*

Context7 will retrieve current documentation directly from official sources, ensuring your AI assistant has accurate information when helping you code.

## 📄 License

MIT
