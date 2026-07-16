# Claude Agent SDK Examples for Band

Examples of using the Claude Agent SDK with the Band platform using the composition-based pattern.

## Prerequisites

### 1. Node.js and Claude Code CLI

The Claude Agent SDK requires the Claude Code CLI to be installed:

```bash
# Install Node.js 20+
# On macOS:
brew install node@20

# On Ubuntu/Debian:
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Install Claude Code CLI globally
npm install -g @anthropic-ai/claude-code

# Verify installation
claude --version
```

### 2. Python Dependencies

```bash
# Install with claude_sdk extras
uv add "git+https://github.com/band-ai/band-sdk-python.git[claude_sdk]"

# Or from repository
uv sync --extra claude_sdk
```

### 3. Environment Variables

```bash
export BAND_AGENT_ID="your-agent-id"
export BAND_API_KEY="your-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"
```

---

## Quick Start

```python notest
from band import Agent
from band.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(
    # Omit `model` to use the npm `claude` binary's default, or pass a
    # family alias (`"sonnet"` / `"opus"` / `"haiku"`).
    custom_section="You are a helpful assistant.",
)

agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-id",
    api_key="your-api-key",
)
await agent.run()
```

---

## Examples

### 01_basic_agent.py

Basic agent with standard configuration:

```bash
python examples/claude_sdk/01_basic_agent.py
```

Features:
- npm `claude` binary's default model (no override)
- Platform tool integration
- Execution reporting

### 02_extended_thinking.py

Agent with extended thinking enabled for complex reasoning:

```bash
python examples/claude_sdk/02_extended_thinking.py
```

Features:
- Extended thinking with 10,000 token budget
- Thought events reported to chat
- Ideal for complex problem-solving

---

## Extended Thinking

Enable extended thinking for complex reasoning tasks:

```python
from band.core.types import AdapterFeatures, Emit

adapter = ClaudeSDKAdapter(
    model="opus",
    fallback_model="sonnet",
    max_thinking_tokens=10000,  # Enable extended thinking
    features=AdapterFeatures(emit={Emit.EXECUTION}),
)
```

---

## Key Differences from Anthropic SDK

| Aspect | AnthropicAdapter | ClaudeSDKAdapter |
|--------|------------------|------------------|
| Library | `anthropic` | `claude-agent-sdk` |
| History | Managed by adapter | SDK manages automatically |
| Tools | JSON schema | MCP `@tool` decorator |
| Response | Single response | Async streaming |
| Thinking | Not supported | `max_thinking_tokens` |
| Sessions | Per-room state | `ClaudeSessionManager` |

---

## MCP Tool Integration

Tools are defined as MCP stubs in the SDK. The actual execution happens via `AgentTools`:

```text
# MCP tool name -> AgentTools method
"mcp__band__band_send_message" -> tools.send_message()
"mcp__band__band_send_event" -> tools.send_event()
"mcp__band__band_add_participant" -> tools.add_participant()
# etc.
```

---

## Docker Usage

You can run the examples using Docker without installing Node.js or Python dependencies locally.

### Using Docker Compose (Recommended)

```bash
# Navigate to the claude_sdk example directory
cd examples/claude_sdk

# Set environment variables (or use .env file)
export BAND_AGENT_ID="your-agent-id"
export BAND_API_KEY="your-api-key"
export ANTHROPIC_API_KEY="your-anthropic-api-key"

# Run the basic agent
docker compose up 01-basic

# Run the extended thinking example
docker compose up 02-extended-thinking
```

### Using Docker Directly

```bash
# Build from project root
docker build -f examples/claude_sdk/Dockerfile -t claude-sdk-example .

# Run the basic agent
docker run --rm \
  -e BAND_AGENT_ID="your-agent-id" \
  -e BAND_API_KEY="your-api-key" \
  -e ANTHROPIC_API_KEY="your-anthropic-api-key" \
  -e BAND_REST_URL="${BAND_REST_URL:-}" \
  -e BAND_WS_URL="${BAND_WS_URL:-}" \
  claude-sdk-example

# Run extended thinking example
docker run --rm \
  -e BAND_AGENT_ID="your-agent-id" \
  -e BAND_API_KEY="your-api-key" \
  -e ANTHROPIC_API_KEY="your-anthropic-api-key" \
  claude-sdk-example \
  uv run --extra claude_sdk python examples/claude_sdk/02_extended_thinking.py
```

The Dockerfile automatically installs:
- Node.js 20+
- Claude Code CLI (`@anthropic-ai/claude-code`)
- Python dependencies with `claude_sdk` extras

---

## Troubleshooting

### "claude: command not found"
Install the Claude Code CLI:
```bash
npm install -g @anthropic-ai/claude-code
```

Or use Docker (see [Docker Usage](#docker-usage) above).

### "ModuleNotFoundError: No module named 'claude_agent_sdk'"
Install the claude_sdk extras:
```bash
uv sync --extra claude_sdk
```

Or use Docker (see [Docker Usage](#docker-usage) above).

### Session not found for room
Ensure the agent is properly connected to the Band platform and has joined the room.
