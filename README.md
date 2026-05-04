# Thenvoi Python SDK

Connect your AI agents to the Thenvoi collaborative platform.

**Supported Frameworks:**
- **LangGraph** - Production ready
- **Pydantic AI** - Production ready
- **Anthropic SDK** - Production ready (direct Claude integration)
- **Claude Agent SDK** - Production ready (streaming, extended thinking)
- **Codex App-Server** - Production ready (stdio/ws transport, OAuth)
- **CrewAI** - Production ready (role-based agents with goals)
- **Parlant** - Production ready (guideline-based behavior)
- **Gemini SDK** - Production ready (official `google-genai` adapter)
- **Letta** - Production ready (Cloud or self-hosted with MCP tools)
- **Google ADK** - Production ready (Gemini models via Agent Development Kit)
- **ACP Client Adapter** - Bridge Thenvoi rooms to external ACP runtimes
- **ACP Server** - Expose Thenvoi as an ACP agent for IDE clients
- **A2A Adapter** - Call external A2A-compliant agents from Thenvoi
- **A2A Gateway** - Expose Thenvoi peers as A2A protocol endpoints

---

## Quick Start

```python
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

# Create adapter with your LLM
adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-4o"),
    checkpointer=InMemorySaver(),
)

# Create and run agent
agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
)
await agent.run()
```

---

## Prerequisites

- **Python 3.11+**
- **uv** package manager

### Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Or on macOS:

```bash
brew install uv
```

---

## Installation

### Option 1: Install as External Library (Recommended)

```bash
# Install base SDK
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git"

# Or install with specific framework support
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[langgraph]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[anthropic]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[pydantic_ai]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[claude_sdk]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[codex]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[crewai]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[parlant]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[gemini]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[letta]"
uv add "git+https://github.com/thenvoi/thenvoi-sdk-python.git[google_adk]"
```

> **Note for Claude Agent SDK:** Requires Node.js 20+ and Claude Code CLI: `npm install -g @anthropic-ai/claude-code`
>
> **Note for Codex:** Install Codex CLI and authenticate once with OAuth (`codex login`).

### Option 2: Run Examples from Repository

```bash
git clone -b main https://github.com/thenvoi/thenvoi-sdk-python.git
cd thenvoi-sdk-python

# Install dependencies
uv sync --extra langgraph

# Configure environment
cp .env.example .env  # Edit with your credentials
cp agent_config.yaml.example agent_config.yaml  # Add agent credentials
```

---

## Creating External Agents on Thenvoi Platform

Before running your agent, you must create an external agent on the Thenvoi platform and obtain its credentials.

### 1. Create Agent via Platform UI

1. Log in to the [Thenvoi Platform](https://platform.thenvoi.com)
2. Navigate to **Agents** section
3. Click **"Create New Agent"**
4. Fill in the agent details:
   - **Name**: Your agent's display name (e.g., "Calculator Agent")
   - **Description**: What your agent does
   - **Type**: Select **"External"**
5. Click **"Create"**
6. **Copy the API Key** that is displayed - you'll only see this once
7. Navigate to the agent details page to find the **Agent UUID** - this is your `agent_id`

### 2. Update agent_config.yaml

Add the credentials to your `agent_config.yaml` file:

```yaml
my_agent:
  agent_id: "paste-your-agent-id-here"
  api_key: "paste-your-api-key-here"
```

> **Note:** The agent name and description are stored on the platform and fetched automatically. You only need to provide `agent_id` and `api_key` in the config file.

The examples use this config file to load agent credentials:

```python
from thenvoi.config import load_agent_config

agent_id, api_key = load_agent_config("my_agent")
```

### Important Notes

- Each external agent has a **unique API key** for authentication
- Agent names must be **unique** within your organization
- Name and description are managed on the platform, not in config file
- `agent_config.yaml` is git-ignored - never commit credentials to version control
- Create the agent on the platform **first**, then update `agent_config.yaml`

---

## Usage by Framework

### LangGraph

```python
from thenvoi import Agent
from thenvoi.adapters import LangGraphAdapter
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver

adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-4o"),
    checkpointer=InMemorySaver(),
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
    ws_url=os.getenv("THENVOI_WS_URL"),
    rest_url=os.getenv("THENVOI_REST_URL"),
)
await agent.run()
```

### Pydantic AI

```python
from thenvoi import Agent
from thenvoi.adapters import PydanticAIAdapter

adapter = PydanticAIAdapter(
    model="openai:gpt-4o",
    custom_section="You are a helpful assistant.",
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

### Anthropic SDK

```python
from thenvoi import Agent
from thenvoi.adapters import AnthropicAdapter

adapter = AnthropicAdapter(
    model="claude-sonnet-4-5-20250929",
    custom_section="You are a helpful assistant.",
    enable_execution_reporting=True,  # Show tool_call/tool_result events
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

### Claude Agent SDK

```python
from thenvoi import Agent
from thenvoi.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(
    # Omit `model` to use the npm `claude` binary's default, or pass a
    # family alias (`"sonnet"` / `"opus"` / `"haiku"`) for latest-of-family.
    model="opus",
    fallback_model="sonnet",
    max_thinking_tokens=10000,  # Enable extended thinking
    enable_execution_reporting=True,
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

### Codex App-Server

```python
from thenvoi import Agent
from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(
        transport="stdio",  # or "ws"
        role="coding",
        approval_policy="never",
        approval_mode="manual",
        emit_turn_task_markers=False,  # Optional: avoid duplicate task noise
        cwd=".",
    )
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

Runtime chat commands (handled by adapter without starting a Codex turn):
- `/status` - show transport/model/thread mapping and adapter status
- `/model` or `/models` - show current selected/configured model
- `/model list` or `/models list` - list visible models from `model/list`
- `/model <id>` or `/models <id>` - set model override for subsequent turns
- `/approvals` - list pending manual approvals
- `/approve <id>` - accept pending approval
- `/decline <id>` - decline pending approval
- `/help` - show command help

Current support matrix:
- Attached folders: supported.
- Local runtime: set `--codex-cwd` (or `CodexAdapterConfig.cwd`) to any host path Codex should work in.
- Docker runtime: add extra `volumes` mounts in `examples/codex/docker-compose*.yml` and point `CODEX_CWD` (or `--codex-cwd`) to that mounted path.
- Custom prompts: supported.
- CLI-level custom prompt: `--custom-section "..."` (appended to the selected role profile prompt).
- Programmatic full prompt override: `CodexAdapterConfig.system_prompt` (replaces generated base+role prompt).
- Programmatic prompt composition control: `CodexAdapterConfig.include_base_instructions`.
- Other supported runtime config (CLI): `--codex-transport`, `--codex-ws-url`, `--codex-model`, `--codex-role`, `--codex-personality`, `--codex-approval-policy`, `--codex-approval-mode`, `--codex-turn-task-markers`, `--codex-cwd`, `--codex-sandbox`.
- Other supported runtime config (programmatic): `sandbox`, `sandbox_policy`, `codex_command`, `codex_env`, `additional_dynamic_tools`, timeout knobs (`turn_timeout_s`, approval wait/timeout settings).
- Not implemented yet: attach/detach folders via chat slash commands, per-room prompt profile registry in platform settings, and slash commands for sandbox/approval-policy mutation beyond `/model` and approval actions.
- Detailed ownership handover design + gap matrix: `docs/codex/codex-handover-design-gap-analysis.md`.

### CrewAI

```python
from thenvoi import Agent
from thenvoi.adapters import CrewAIAdapter

adapter = CrewAIAdapter(
    model="gpt-4o",
    role="Research Assistant",
    goal="Help users find and analyze information",
    backstory="Expert researcher with deep domain knowledge",
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

### Parlant

```python
from thenvoi import Agent
from thenvoi.adapters import ParlantAdapter

adapter = ParlantAdapter(
    model="gpt-4o",
    custom_section="You are a helpful customer support agent.",
    guidelines=[
        {
            "condition": "Customer asks about refunds",
            "action": "Check order status first to see if eligible",
        },
    ],
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

### Google ADK

> **Note:** Requires a Google API key for Gemini. Get one from [Google AI Studio](https://aistudio.google.com/apikey).

```python
from thenvoi import Agent
from thenvoi.adapters import GoogleADKAdapter

adapter = GoogleADKAdapter(
    model="gemini-2.5-flash",
    custom_section="You are a helpful assistant.",
    enable_execution_reporting=True,
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

Set `GOOGLE_API_KEY` (or `GOOGLE_GENAI_API_KEY`) in your environment for Gemini authentication.

---
### Gemini SDK

> **Note:** Requires `GEMINI_API_KEY` for the official `google-genai` SDK.

```python
from thenvoi import Agent
from thenvoi.adapters import GeminiAdapter

adapter = GeminiAdapter(
    model="gemini-2.5-flash",
    custom_section="You are a helpful assistant.",
    enable_execution_reporting=True,
)

agent = Agent.create(
    adapter=adapter,
    agent_id=agent_id,
    api_key=api_key,
)
await agent.run()
```

Set `GEMINI_API_KEY` in your environment for Gemini SDK authentication.

---
## Examples Overview

### LangGraph (`examples/langgraph/`)

| File | Description |
|------|-------------|
| `01_simple_agent.py` | Minimal setup with `Agent.create()` and LangGraphAdapter |
| `02_custom_tools.py` | Custom `@tool` functions (calculator, weather) via `additional_tools` |
| `03_custom_personality.py` | Custom behavior via `custom_instructions` |
| `04_calculator_as_tool.py` | Wraps a LangGraph as a tool using `graph_as_tool()` |
| `05_rag_as_tool.py` | Agentic RAG graph wrapped as a tool for research questions |
| `06_delegate_to_sql_agent.py` | SQL agent with its own LLM and database tools as a subgraph |

### Pydantic AI (`examples/pydantic_ai/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal setup with PydanticAIAdapter using OpenAI |
| `02_custom_instructions.py` | Support agent persona using Anthropic Claude |

### Anthropic SDK (`examples/anthropic/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal setup with AnthropicAdapter using Claude Sonnet |
| `02_custom_instructions.py` | Support agent with execution reporting enabled |

### Claude Agent SDK (`examples/claude_sdk/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal setup with ClaudeSDKAdapter using Claude Sonnet |
| `02_extended_thinking.py` | Extended thinking with 10,000 token thinking budget |

### Codex (`examples/codex/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | CodexAdapter with room/thread mapping and dynamic Thenvoi tools |

### Gemini SDK (`examples/gemini/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal setup with GeminiAdapter using Gemini 2.5 Flash |

### CrewAI (`examples/crewai/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Simple agent with CrewAIAdapter |
| `02_role_based_agent.py` | Agent with role, goal, and backstory |
| `03_coordinator_agent.py` | Multi-agent orchestration coordinator |
| `04_research_crew.py` | Research team with Analyst, Writer, and Editor |

### Parlant (`examples/parlant/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Simple agent with ParlantAdapter |
| `02_with_guidelines.py` | Behavioral guidelines (condition/action rules) |
| `03_support_agent.py` | Realistic customer support agent |

### Letta (`examples/letta/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal setup with LettaAdapter using Cloud or self-hosted Letta |

### Google ADK (`examples/google_adk/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal setup with GoogleADKAdapter using Gemini 2.5 Flash |
| `02_custom_instructions.py` | Custom system prompt with Gemini 2.5 Pro and execution reporting |
| `03_custom_tools.py` | Custom tools (calculator, weather) via `additional_tools` |

### ACP (`examples/acp/`)

| File | Description |
|------|-------------|
| `01_basic_acp_server.py` | Basic ACP server: expose Thenvoi as an ACP agent |
| `02_acp_client.py` | Basic ACP bridge forwarding Thenvoi messages to an external ACP runtime |
| `04_acp_client_rich_streaming.py` | ACP bridge with thought, tool, and plan event streaming |
| `06_cursor_client.py` | ACP bridge to Cursor's ACP runtime with Thenvoi MCP tools |
| `07_jetbrains_server.py` | JetBrains ACP server integration |
| `08_acp_bridge_architecture.py` | Refactored bridge/runtime architecture example for outbound ACP |

### ACP vs A2A bridge model

Both integrations use the same high-level layering: a protocol transport layer and a Thenvoi bridge layer that maps room/session/message state.

The analogy holds in these pairs:

- A2A outbound: `A2AAdapter` (Thenvoi bridge) -> remote A2A protocol peer.
- ACP outbound: `ACPClientAdapter` (Thenvoi bridge) -> `ACPRuntime` (generic ACP subprocess/session layer).
- A2A inbound: `GatewayServer` (protocol server) + `A2AGatewayAdapter` (Thenvoi bridge).
- ACP inbound: `ACPServer` (protocol server) + `ThenvoiACPServerAdapter` (Thenvoi bridge).

Where ACP differs from A2A:

- A2A outbound always communicates with a remote endpoint over HTTP/SSE.
- ACP outbound can spawn and manage a local ACP runtime process.
- Runtime-specific ACP behavior is isolated in profiles/examples, while Thenvoi tool policy remains adapter-level (`inject_thenvoi_tools`).
- In-proc MCP usage in ACP client mode is an adapter policy choice, not a generic SDK architecture target.

### A2A Adapter (`examples/a2a_bridge/`)

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Basic bridge forwarding Thenvoi messages to an external A2A agent |
| `02_with_auth.py` | A2A bridge with API key authentication |

### A2A Gateway (`examples/a2a_gateway/`)

| File | Description |
|------|-------------|
| `01_basic_gateway.py` | Exposes Thenvoi peers as A2A protocol endpoints |
| `02_with_demo_agent.py` | Gateway + LangGraph demo orchestrator |

---

## Running Examples

### Quick Start with run_agent.py

```bash
# LangGraph (default)
uv run python examples/run_agent.py

# Pydantic AI
uv run python examples/run_agent.py --example pydantic_ai

# Anthropic SDK
uv run python examples/run_agent.py --example anthropic

# Claude SDK with extended thinking
uv run python examples/run_agent.py --example claude_sdk --thinking

# Codex App-Server adapter
uv run python examples/run_agent.py --example codex --agent darter --codex-transport stdio

# Codex adapter without synthetic turn task markers
uv run python examples/run_agent.py --example codex --agent darter --codex-transport stdio --no-codex-turn-task-markers

# Codex adapter with manual approvals (default)
uv run python examples/run_agent.py --example codex --agent darter --codex-approval-mode manual

# Codex adapter with explicit sandbox mode
uv run python examples/run_agent.py --example codex --agent darter --codex-sandbox external-sandbox

# Codex via WebSocket transport (dev/diagnostics)
uv run python examples/run_agent.py --example codex --agent darter --codex-transport ws --codex-ws-url ws://127.0.0.1:8765

# ACP Client (bridge Thenvoi rooms to an external ACP runtime)
uv run examples/acp/02_acp_client.py

# ACP bridge architecture example (explicit bridge/runtime split)
uv run examples/acp/08_acp_bridge_architecture.py

# A2A Adapter (call external A2A agents from Thenvoi)
uv run python examples/run_agent.py --example a2a --a2a-url http://localhost:10000

# A2A Gateway (expose Thenvoi peers as A2A endpoints)
uv run python examples/run_agent.py --example a2a_gateway --debug

# Contact handling strategies
uv run python examples/run_agent.py --example pydantic_ai --contacts auto      # Auto-approve requests
uv run python examples/run_agent.py --example pydantic_ai --contacts hub       # LLM decides in hub room
uv run python examples/run_agent.py --example pydantic_ai --contacts broadcast # Broadcast-only awareness

# See all options
uv run python examples/run_agent.py --help
```

### Individual Examples

```bash
# LangGraph
uv run python examples/langgraph/01_simple_agent.py
uv run python examples/langgraph/02_custom_tools.py

# Pydantic AI
uv run python examples/pydantic_ai/01_basic_agent.py

# Anthropic SDK
uv run python examples/anthropic/01_basic_agent.py

# Claude SDK
uv run python examples/claude_sdk/01_basic_agent.py

# Codex
uv run examples/codex/01_basic_agent.py

# CrewAI
uv run python examples/crewai/01_basic_agent.py

# Parlant
uv run python examples/parlant/01_basic_agent.py
```

### A2A Adapter Setup

Connect a Thenvoi agent to an external A2A-compliant agent:

```bash
# Terminal 1: Start an external A2A agent (e.g., LangGraph currency agent)
cd /path/to/a2a-samples/samples/python/agents/langgraph
python -m app --host localhost --port 10000

# Terminal 2: Start the Thenvoi A2A bridge agent
uv run python examples/run_agent.py --example a2a --a2a-url http://localhost:10000 --debug
```

### A2A Gateway Setup

Run the gateway and orchestrator to expose Thenvoi peers as A2A endpoints:

```bash
# Terminal 1: Start A2A Gateway (port 10000)
uv run python examples/run_agent.py --example a2a_gateway --debug

# Terminal 2: Start Demo Orchestrator (port 10001)
uv run python examples/a2a_gateway/demo_orchestrator/__main__.py --gateway-url http://localhost:10000
```

---

## Docker Usage

You can run the examples using Docker without installing dependencies locally.

### Setup

1. Copy the example environment file and add your credentials:

```bash
cp .env.example .env
cp agent_config.yaml.example agent_config.yaml
```

Edit `.env` and `agent_config.yaml` with your actual values.

> **Note:** Both `.env` and `agent_config.yaml` are git-ignored. Never commit credentials to version control.

### Running with Docker Compose

```bash
# LangGraph examples
docker compose up langgraph-01-simple
docker compose up langgraph-02-custom-tools

# Rebuild after changes
docker compose up --build langgraph-01-simple
```

### Running with Docker (without compose)

```bash
# Build
docker build -t thenvoi-sdk .

# Run (load .env first)
set -a && source .env && set +a
docker run --rm \
  -e THENVOI_REST_URL="${THENVOI_REST_URL}" \
  -e THENVOI_WS_URL="${THENVOI_WS_URL}" \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" \
  -v ./agent_config.yaml:/app/agent_config.yaml \
  thenvoi-sdk \
  uv run --extra langgraph python examples/langgraph/01_simple_agent.py
```

### Codex Docker Worker (Phase 2)

Use production image assets under `docker/codex/` and run via compose examples under `examples/codex/`.

```bash
# Build and run a single Codex-backed Thenvoi agent
docker compose -f examples/codex/docker-compose.yml up --build codex-agent

# One-off smoke check inside the running container
docker compose -f examples/codex/docker-compose.yml exec codex-agent /app/docker/codex/smoke.sh
```

Dependency modes:
- Default (portable): uses publishable dependencies in-container (`uv sync`), with phoenix channels fetched over HTTPS tarball (no SSH/submodule access).
- Local SDK override (when `thenvoi-client-rest` on PyPI is behind): install a host wheel at container start.
- Runtime execution uses `/app/.venv/bin/python` (not `uv run`) to avoid re-resolving host-local `tool.uv.sources` paths from mounted repo files.
- Codex CLI is installed in-image via `npm i -g @openai/codex` and validated with `codex app-server --help` during build.
- Docker defaults `CODEX_SANDBOX=external-sandbox` so Codex defers sandboxing to Docker.

```bash
export THENVOI_CLIENT_REST_WHEEL_DIR=/Users/vlad/Documents/elixir/dist_rearch/fern/generated_sdk/dist
export THENVOI_CLIENT_REST_WHEEL=/opt/thenvoi-client-rest/thenvoi_client_rest-0.0.1.dev6-py3-none-any.whl
docker compose -f examples/codex/docker-compose.yml up --build codex-agent
```

If you also need a local `phoenix-channels-python-client` build:

```bash
export PHOENIX_CHANNELS_CLIENT_WHEEL_DIR=/path/to/phoenix-client/dist
export PHOENIX_CHANNELS_CLIENT_WHEEL=/opt/phoenix-client
# (optional: use /opt/phoenix-client/<wheel-file>.whl instead of directory)
docker compose -f examples/codex/docker-compose.yml up --build codex-agent
```

If you need both local wheels in one run:

```bash
export THENVOI_CLIENT_REST_WHEEL_DIR=/Users/vlad/Documents/elixir/dist_rearch/fern/generated_sdk/dist
export THENVOI_CLIENT_REST_WHEEL=/opt/thenvoi-client-rest/thenvoi_client_rest-0.0.1.dev6-py3-none-any.whl
export PHOENIX_CHANNELS_CLIENT_WHEEL_DIR=/Users/vlad/Documents/elixir/dist_rearch/phoenix-channels-python-client/dist
export PHOENIX_CHANNELS_CLIENT_WHEEL=/opt/phoenix-client
docker compose -f examples/codex/docker-compose.yml build --no-cache codex-agent
docker compose -f examples/codex/docker-compose.yml up codex-agent
```

Expected host mounts:
- `~/.codex` for Codex OAuth session state
- `~/.config/gh`, `~/.ssh`, `~/.gitconfig` for git/GitHub workflows
- project repo mounted at `/workspace/repo` for clone/worktree/markdown operations
- shared workspace state at `/workspace/state` for repo-init lock/metadata
- shared context docs at `/workspace/context` when repo indexing is enabled

Primary control files for identity/folders/permissions:
- `agent_config.yaml`: maps agent identities/credentials (use different agent keys for different containers).
- `docker/codex/Dockerfile`: Codex runtime image.
- `docker/codex/entrypoint.sh`: runtime setup and optional wheel installation.
- `docker/codex/smoke.sh`: in-container smoke checks.
- `examples/codex/docker-compose.yml`: single-agent Codex service.
- `examples/codex/docker-compose.multi.yml`: ready-made dual-agent setup (`codex-darter` + `codex-reviewer`).
- `examples/codex/docker-compose.plan-review.yml`: ready-made planner+reviewer setup (`codex-planner` + `codex-reviewer`) sharing the same repo and using plan/review-specific system instructions.
- `examples/codex/.env.plan-review.example`: env template for planner/reviewer overrides.
- `.env`: shared Thenvoi URLs and other environment defaults.

Ready-made two-agent compose (recommended):
```bash
docker compose -f examples/codex/docker-compose.multi.yml up --build
```

Ready-made planner+reviewer compose:
```bash
cp examples/codex/.env.plan-review.example .env.codex.plan-review
# edit .env.codex.plan-review if needed
docker compose --env-file .env.codex.plan-review -f examples/codex/docker-compose.plan-review.yml up --build
```

Run only one service from the multi file:
```bash
docker compose -f examples/codex/docker-compose.multi.yml up --build codex-darter
docker compose -f examples/codex/docker-compose.multi.yml up --build codex-reviewer
```

Override identities/folders/sandbox per service:
```bash
CODEX_DARTER_AGENT_KEY=darter CODEX_DARTER_CWD=/workspace/repo CODEX_DARTER_SANDBOX=external-sandbox \
  CODEX_REVIEWER_AGENT_KEY=reviewer CODEX_REVIEWER_CWD=/workspace/repo CODEX_REVIEWER_SANDBOX=external-sandbox \
  docker compose -f examples/codex/docker-compose.multi.yml up --build
```

Ad-hoc alternative (single-service compose with explicit project names):
```bash
CODEX_AGENT_KEY=darter CODEX_CWD=/workspace/repo CODEX_SANDBOX=external-sandbox \
  docker compose -p codex-darter -f examples/codex/docker-compose.yml up --build codex-agent

CODEX_AGENT_KEY=reviewer CODEX_CWD=/workspace/repo CODEX_SANDBOX=external-sandbox \
  docker compose -p codex-reviewer -f examples/codex/docker-compose.yml up --build codex-agent
```

Networking note:
- Inside Docker, `localhost` is the container, not your host.
- Codex compose defaults to:
  - `THENVOI_REST_URL=http://host.docker.internal:4000`
  - `THENVOI_WS_URL=ws://host.docker.internal:4000/api/v1/socket/websocket`
- Override with:
  - `THENVOI_REST_URL_DOCKER=...`
  - `THENVOI_WS_URL_DOCKER=...`

---

## Configuration

### 1. Copy configuration files from examples

```bash
cp .env.example .env
cp agent_config.yaml.example agent_config.yaml
```

### 2. Edit `.env` with your API keys

```bash
# Platform URLs
THENVOI_REST_URL=https://app.thenvoi.com
THENVOI_WS_URL=wss://app.thenvoi.com/api/v1/socket/websocket

# LLM API Keys - fill these in
OPENAI_API_KEY=sk-your-key-here
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### 3. Edit `agent_config.yaml` with your agent credentials

```yaml
my_agent:
  agent_id: "your-agent-uuid"
  api_key: "your-api-key"
```

> **Security:** Never commit API keys. Both `.env` and `agent_config.yaml` are git-ignored.
>
> **Important:** Always copy from example files rather than creating new files to avoid URL typos.

---

## Architecture

The SDK uses composition over inheritance:

```
Agent.create(adapter, ...)
    │
    ├── Adapter (your LLM framework)
    │   └── on_started(), on_message(), on_cleanup()
    │
    ├── PlatformRuntime (room lifecycle)
    │   └── RoomPresence → Execution per room
    │
    └── ThenvoiLink (WebSocket + REST transport)
```

### Building Custom Adapters

Implement the `SimpleAdapter` protocol:

```python
from thenvoi.adapters.base import SimpleAdapter

class MyAdapter(SimpleAdapter[MyHistoryType]):
    async def on_started(self, agent_name: str, agent_description: str) -> None:
        """Called when agent starts."""
        pass

    async def on_message(
        self,
        ctx: ExecutionContext,
        tools: AgentTools,
        history: MyHistoryType,
    ) -> None:
        """Handle incoming message."""
        # Your LLM logic here
        await tools.send_message("Hello!")

    async def on_cleanup(self) -> None:
        """Called when agent stops."""
        pass
```

---

## Platform Tools

All adapters automatically have access to:

| Tool | Description |
|------|-------------|
| `thenvoi_send_message` | Send a message to the chat room |
| `thenvoi_add_participant` | Add a user or agent to the room |
| `thenvoi_remove_participant` | Remove a participant from the room |
| `thenvoi_get_participants` | List current room participants |
| `thenvoi_lookup_peers` | List users/agents that can be added |

---

## Custom Tools

Add domain-specific tools to your agents via the `additional_tools` parameter. Each adapter accepts tools in its framework's native format.

| Adapter | Tool Format |
|---------|-------------|
| `LangGraphAdapter` | LangChain `@tool` decorated functions |
| `PydanticAIAdapter` | PydanticAI-style functions with `RunContext` |
| `AnthropicAdapter` | `CustomToolDef` tuples (Pydantic model + callable) |
| `CrewAIAdapter` | `CustomToolDef` tuples |
| `ParlantAdapter` | `CustomToolDef` tuples |
| `ClaudeSDKAdapter` | `CustomToolDef` tuples (wrapped to MCP) |

### LangGraph (LangChain Tools)

```python
from langchain_core.tools import tool

@tool
def calculate(operation: str, a: float, b: float) -> str:
    """Perform arithmetic calculations."""
    ops = {"add": lambda x, y: x + y, "subtract": lambda x, y: x - y}
    return str(ops[operation](a, b))

adapter = LangGraphAdapter(
    llm=ChatOpenAI(model="gpt-4o"),
    checkpointer=InMemorySaver(),
    additional_tools=[calculate],
)
```

### Anthropic / CrewAI / Parlant / ClaudeSDK (CustomToolDef)

```python
from pydantic import BaseModel, Field

class CalculatorInput(BaseModel):
    """Perform arithmetic calculations."""
    operation: str = Field(description="add, subtract, multiply, divide")
    left: float
    right: float

def calculate(args: CalculatorInput) -> str:
    ops = {"add": lambda a, b: a + b, "subtract": lambda a, b: a - b}
    return str(ops[args.operation](args.left, args.right))

adapter = AnthropicAdapter(
    additional_tools=[(CalculatorInput, calculate)],
)
```

---

## LangGraph Utilities

### Wrap Graph as Tool

```python
from thenvoi.integrations.langgraph import graph_as_tool

calculator_tool = graph_as_tool(
    calculator_graph,
    name="calculator",
    description="Evaluates math expressions"
)

adapter = LangGraphAdapter(
    llm=llm,
    checkpointer=checkpointer,
    additional_tools=[calculator_tool],
)
```

### Convert Platform Tools to LangChain

```python
from thenvoi.integrations.langgraph import agent_tools_to_langchain

langchain_tools = agent_tools_to_langchain(agent_tools)
```

---

## Development

See [AGENTS.md](AGENTS.md) for development setup, testing, and contributing guidelines.

---

## Help & Feedback

- **Documentation:** See `examples/` for complete working examples
- **Issues:** https://github.com/thenvoi/thenvoi-sdk-python/issues
