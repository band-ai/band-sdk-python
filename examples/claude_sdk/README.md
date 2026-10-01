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

### 3. Credentials

Add the agent credentials to `agent_config.yaml` in the working directory
(`claude_sdk_agent` for 01/02, `tom_agent`/`jerry_agent` for 03/04):

```yaml
claude_sdk_agent:
  agent_id: "your-agent-id"
  api_key: "your-band-api-key"
```

Then set the model key and platform URLs in the environment (or `.env`):

```bash
export ANTHROPIC_API_KEY="your-anthropic-api-key"
export BAND_WS_URL="wss://app.band.ai/api/v1/socket/websocket"
export BAND_REST_URL="https://app.band.ai"
```

---

## Quick Start

```python notest
from band import Agent
from band.adapters import ClaudeSDKAdapter, ClaudeSDKAdapterConfig

adapter = ClaudeSDKAdapter(
    # Omit `model` to use the adapter's pinned default, or pass a
    # family alias (`"sonnet"` / `"opus"` / `"haiku"`).
    ClaudeSDKAdapterConfig(custom_section="You are a helpful assistant."),
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
- The adapter's pinned default model (no `model=` override)
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
from band.adapters import ClaudeSDKAdapter, ClaudeSDKAdapterConfig
from band.core.types import Emit

adapter = ClaudeSDKAdapter(
    ClaudeSDKAdapterConfig(
        model="opus",
        fallback_model="sonnet",
        max_thinking_tokens=10000,  # Enable extended thinking
    ),
    emit=Emit.TOOL_CALLS | Emit.THOUGHTS,  # Report tool calls and thinking as events
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

To run a Claude SDK agent in Docker without installing Node.js or Python
locally, use [`examples/claude_sdk_docker`](../claude_sdk_docker/README.md).

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
