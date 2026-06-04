# Claude Agent SDK Adapter

The [Claude Agent SDK](https://docs.anthropic.com/en/docs/agents/claude-code-sdk-overview), also known as the Claude Code SDK, runs Claude Code as a programmable agent. The Band Claude SDK adapter is for building a Band collaborator that runs with Claude Code's built-in tools and capabilities, such as terminal use, filesystem access, skills, MCP tools, and extended thinking.

Use this adapter when the agent should take part in Band conversations while working in a codebase: inspect files, edit files, run commands, and ask for approvals. Use the [Anthropic adapter](anthropic.md) instead for lightweight Claude chat/tool agents that call the Anthropic API directly. Use the [Codex adapter](codex.md) for an OpenAI-powered coding agent with Codex transport, sandbox, task lifecycle, and telemetry controls. Use the [LangGraph adapter](langgraph.md) when you already have a LangChain/LangGraph workflow.

## Install

```bash
uv add "band-sdk[claude_sdk]"
```

## Prerequisites

The adapter starts Claude Code as a subprocess. Install and authenticate Claude Code before running your Band agent:

```bash
npm install -g @anthropic-ai/claude-code
claude auth login
```

Requires Node.js 20+.

You need two credentials or auth contexts:

- A Band platform API key for `Agent.create(api_key=...)`.
- Claude Code authentication for the subprocess. Use `claude auth login`, or set `ANTHROPIC_API_KEY` if that is how your Claude Code environment is configured.

Credentials for Band can also be loaded from `agent_config.yaml` with `Agent.from_config("my_agent", adapter=adapter)`.

## Quick Start

```python
import asyncio
import os

from band import Agent
from band.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(
    cwd=os.getcwd(),
    approval_mode="manual",
)

agent = Agent.create(
    adapter=adapter,
    agent_id="your-agent-uuid",
    api_key="your-band-api-key",
    ws_url="wss://app.band.ai/api/v1/socket/websocket",
    rest_url="https://app.band.ai",
)

asyncio.run(agent.run())
```

## Where Parameters Go

The quick start uses two setup calls:

- `ClaudeSDKAdapter(...)` configures Claude Code: working directory, model settings, permission mode, chat-based approvals, custom tools, feature flags, and Claude Code runtime behavior. The [Configuration Reference](#configuration-reference) below covers these parameters.
- `Agent.create(...)` connects that configured adapter to Band. Use it for the Band agent identity, Band API key, platform URLs, session settings, contact-event handling, callbacks, and preprocessing.

Claude Code authentication is handled by the `claude` binary or `ANTHROPIC_API_KEY`. `Agent.create(api_key=...)` is only the Band platform key.

Common `Agent.create(...)` parameters:

| Parameter | Use it for |
|-----------|------------|
| `adapter` | The configured `ClaudeSDKAdapter` instance. |
| `agent_id` | The Band agent UUID to run as. |
| `api_key` | The Band platform API key. |
| `ws_url` | Band WebSocket URL. Omit it to use the hosted default. |
| `rest_url` | Band REST API URL. Omit it to use the hosted default. |
| `config` | Advanced Band runtime options. Most agents do not need it. |
| `session_config` | Advanced session lifecycle behavior. |
| `contact_config` | How incoming contact requests and contact updates are handled. |
| `on_participant_added` / `on_participant_removed` | Optional callbacks for room membership changes. |
| `preprocessor` | Optional event filter or transformer before messages reach the adapter. |

## How It Works

Each Band room gets its own Claude Code session. On the first message in a room, the adapter gives Claude Code the conversation context and a system prompt with Band collaboration instructions. Then it gives Claude Code an in-process MCP server that exposes Band collaboration tools, optional memory/contact tools, and your custom tools.

Claude Code responses are routed back to the Band room. Through Band tools, it can send messages, look up peers, add participants, and create new chats. If Claude Code asks for permission to edit files or run commands, you can let Claude Code handle that through `permission_mode`, or you can opt into Band chat-based approvals with `approval_mode`.

## Safety Basics

This adapter can edit files and run commands in `cwd`. For production or shared rooms:

- Set `cwd` to a dedicated workspace, not your home directory.
- Prefer `approval_mode="manual"` for rooms with untrusted participants.
- Be deliberate with `permission_mode="bypassPermissions"` and `approval_mode="auto_accept"`; both are high-trust settings.
- Use Docker, a worktree, or another isolation boundary when the agent will modify code.

## Configuration Reference

This section covers `ClaudeSDKAdapter(...)` constructor parameters. Pass these directly to `ClaudeSDKAdapter(...)`, not to `Agent.create(...)`.

### Model and Runtime

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str \| None` | `None` | Claude model. Accepts full IDs or aliases such as `"sonnet"`, `"opus"`, `"haiku"`, and `"inherit"`. When `None`, no `--model` flag is sent and the `claude` binary chooses. |
| `fallback_model` | `str \| None` | `None` | Fallback model for Claude Code if the primary model is unavailable. Aliases are accepted. |
| `max_thinking_tokens` | `int \| None` | `None` | Maximum tokens for Claude extended thinking. |
| `permission_mode` | `"default" \| "acceptEdits" \| "plan" \| "bypassPermissions"` | `"acceptEdits"` | Claude Code's own permission mode for file and command operations. |
| `cwd` | `str \| None` | `None` | Working directory for Claude Code sessions. Must exist if provided. |

### Prompts and Tools

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `custom_section` | `str \| None` | `None` | Custom instructions appended to the generated Band system prompt. |
| `additional_tools` | `list[CustomToolDef] \| None` | `None` | Custom tools as `(PydanticModel, callable)` tuples. They are converted to MCP tools internally. |
| `features` | `AdapterFeatures \| None` | `None` | Optional Band feature settings: extra platform-tool capabilities and telemetry emit options. |
| `history_converter` | `ClaudeSDKHistoryConverter \| None` | auto | Advanced escape hatch for replacing the default room-history converter. |

### Approval Handling

`permission_mode` is Claude Code's native permission setting. `approval_mode` is Band's chat-based approval layer. Leave `approval_mode=None` to use Claude Code's native behavior only. Set `approval_mode="manual"` to route permission requests into the Band room.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `approval_mode` | `None \| "manual" \| "auto_accept" \| "auto_decline"` | `None` | Chat-based approval behavior. `"manual"` asks the room to approve or decline. |
| `approval_text_notifications` | `bool` | `True` | Send room messages for automatic approval or decline decisions. |
| `approval_wait_timeout_s` | `float` | `300.0` | Seconds to wait for a manual approval. |
| `approval_timeout_decision` | `"accept" \| "decline"` | `"decline"` | Decision when manual approval times out. |
| `max_pending_approvals_per_room` | `int` | `50` | Maximum pending approvals per room. Oldest entries are evicted when full. |
| `approval_authorized_senders` | `set[str] \| None` | `None` | Sender IDs allowed to `/approve` and `/decline`. `None` means any room participant. |

When `approval_mode="manual"`, Claude pauses and the adapter posts a message like:

> Approval requested (execute `npm test`). Token: `a-1`.
> Reply `/approve a-1` or `/decline a-1`.
> Use `/approvals` to list pending approvals.

If exactly one approval is pending, the token can be omitted: `/approve` or `/decline`.

| Command | Description |
|---------|-------------|
| `/approve <token>` | Approve a pending permission request. |
| `/decline <token>` | Decline a pending permission request. |
| `/approvals` | List pending approvals with token and age. |
| `/status` | Show model, permission mode, approval mode, pending approvals, and session details. |

## AdapterFeatures: Capabilities and Emit

`AdapterFeatures` is passed to the adapter constructor as `features=AdapterFeatures(...)`. It has two jobs:

- `capabilities` exposes optional Band tool categories to the model.
- `emit` controls telemetry events the adapter sends back to Band.

For this adapter, all capabilities and emit options are off by default.

| Feature | Supported | What it does |
|---------|-----------|--------------|
| `Capability.CONTACTS` | Yes | Exposes contact-management tools to Claude Code. Incoming contact request handling is configured separately with `ContactEventConfig` on `Agent.create(...)`. |
| `Capability.MEMORY` | Yes | Exposes memory tools, if memory is enabled for your Band workspace. |
| `Emit.EXECUTION` | Yes | Sends `tool_call` and `tool_result` events for tool use. |
| `Emit.THOUGHTS` | Yes | Sends `thought` events for completed Claude extended-thinking blocks. These are not streaming deltas. |
| `Emit.TASK_EVENTS` | No | Not supported as a configurable emit option by this adapter. |

Example:

```python
import os

from band import AdapterFeatures, Capability, Emit
from band.adapters import ClaudeSDKAdapter

adapter = ClaudeSDKAdapter(
    cwd=os.getcwd(),
    features=AdapterFeatures(
        capabilities={Capability.CONTACTS, Capability.MEMORY},
        emit={Emit.EXECUTION, Emit.THOUGHTS},
    ),
)
```

## Custom Tools

Use `additional_tools` when you want Claude Code to call functions from your own application. Each custom tool is converted to an MCP tool and made available alongside the Band collaboration tools.

```python
from pydantic import BaseModel, Field

from band.adapters import ClaudeSDKAdapter


class LookupInput(BaseModel):
    """Look up a user by email."""

    email: str = Field(description="User email address")


def lookup_user(args: LookupInput) -> str:
    return f"Found user: {args.email}"


adapter = ClaudeSDKAdapter(
    additional_tools=[(LookupInput, lookup_user)],
)
```

The MCP tool name comes from the Pydantic model class, not the callable name. `LookupInput` becomes `lookup`: the SDK strips a trailing `Input` suffix and lowercases the rest. Choose model class names that produce unique tool names, and avoid names that collide with built-in Band tools.

## Examples

See [examples/claude_sdk/](../../examples/claude_sdk/) for runnable scripts.

| File | Start here when you want to... |
|------|--------------------------------|
| `01_basic_agent.py` | Run a minimal Claude Code backed Band agent. |
| `02_extended_thinking.py` | Enable extended thinking and emit thought events. |
| `03_tom_agent.py` | Run one side of the Tom/Jerry multi-agent demo. |
| `04_jerry_agent.py` | Run the other side of the Tom/Jerry demo. |

For Docker-based deployments, see [examples/claude_sdk_docker/](../../examples/claude_sdk_docker/).
