# Claude Agent SDK Adapter

The [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents/claude-code-sdk-overview) (Claude Code SDK) lets you run Claude Code as a programmable agent. It provides file editing, command execution, and extended thinking through a subprocess-based runtime.

## Install

```bash
uv add "thenvoi-sdk[claude_sdk]"
```

## How It Works

The adapter spawns a Claude Code subprocess per room and manages the session lifecycle. Room history is converted and injected at session start. The adapter generates a system prompt that includes Thenvoi collaboration instructions, converts Thenvoi platform tools to MCP tool format, and routes Claude Code's responses back to the room. When approval mode is enabled, the adapter intercepts Claude Code's permission requests and routes them through the Thenvoi chat room.

## Quick Start

```python
from thenvoi.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(model="sonnet")
```

## Configuration Reference

### Model and Runtime

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str \| None` | `None` | Claude model. Accepts full IDs (`"claude-opus-4-7"`) or aliases (`"sonnet"`, `"opus"`, `"haiku"`, `"inherit"`). When `None`, the `claude` binary picks its own default. |
| `fallback_model` | `str \| None` | `None` | Fallback model when the primary is unavailable. Aliases accepted. |
| `max_thinking_tokens` | `int \| None` | `None` | Max tokens for extended thinking. |
| `permission_mode` | `"default" \| "acceptEdits" \| "plan" \| "bypassPermissions"` | `"acceptEdits"` | Claude Code permission mode for file and command operations. |
| `cwd` | `str \| None` | `None` | Working directory for Claude Code sessions. |

### Prompts and Instructions

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `custom_section` | `str \| None` | `None` | Custom instructions appended to the generated system prompt. |

### Approval Handling

When `approval_mode` is set, Claude Code's permission requests are routed through the Thenvoi chat room instead of being handled by `permission_mode` alone.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `approval_mode` | `None \| "manual" \| "auto_accept" \| "auto_decline"` | `None` | `None` disables chat-based approval. `"manual"` routes to room via `/approve` and `/decline`. |
| `approval_text_notifications` | `bool` | `True` | Send chat messages for auto-approve/decline decisions. |
| `approval_wait_timeout_s` | `float` | `300.0` | Seconds to wait for a manual approval. |
| `approval_timeout_decision` | `"accept" \| "decline"` | `"decline"` | Decision when approval times out. |
| `max_pending_approvals_per_room` | `int` | `50` | Cap on concurrent pending approvals per room. Oldest evicted. |
| `approval_authorized_senders` | `set[str] \| None` | `None` | Sender IDs allowed to `/approve` and `/decline`. `None` means any participant. |

### Other

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `additional_tools` | `list[CustomToolDef]` | `None` | Custom tools as `(PydanticModel, callable)` tuples. Converted to MCP tools internally. |
| `features` | `AdapterFeatures` | `None` | Capabilities and emit options. |
| `history_converter` | `ClaudeSDKHistoryConverter` | auto | Override the default history converter. |

## Capabilities and Emit

| Feature | Supported |
|---------|-----------|
| `Capability.CONTACTS` | Yes |
| `Capability.MEMORY` | Yes |
| `Emit.EXECUTION` | Yes |
| `Emit.THOUGHTS` | Yes |
| `Emit.TASK_EVENTS` | - |

Claude SDK is one of two adapters (alongside Codex) that supports `Emit.THOUGHTS`. When enabled, the adapter emits `thought` events for Claude's thinking blocks and approval-handling decisions. These are completed items, not streaming deltas.

```python
from thenvoi import AdapterFeatures, Emit
from thenvoi.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(
    model="sonnet",
    features=AdapterFeatures(
        emit={Emit.EXECUTION, Emit.THOUGHTS},
    ),
)
```

## Custom Tools

Uses the Thenvoi custom-tool tuple format. Tools are converted to MCP tools internally:

```python
from pydantic import BaseModel, Field

class LookupInput(BaseModel):
    """Look up a user by email."""
    email: str = Field(description="User email address")

def lookup_user(args: LookupInput) -> str:
    return f"Found user: {args.email}"

adapter = ClaudeSDKAdapter(
    model="sonnet",
    additional_tools=[(LookupInput, lookup_user)],
)
```

## Examples

See [examples/claude_sdk/](../../examples/claude_sdk/) for runnable scripts.

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal Claude SDK agent |
| `02_extended_thinking.py` | Extended thinking with `max_thinking_tokens` |
| `03_tom_agent.py` | Tom agent for multi-agent demo |
| `04_jerry_agent.py` | Jerry agent for multi-agent demo |

For Docker-based deployments, see [examples/claude_sdk_docker/](../../examples/claude_sdk_docker/).
