# Codex Adapter

[OpenAI Codex](https://openai.com/codex) is a coding agent runtime that can inspect files, edit files, run commands, and manage approval workflows. The Band Codex adapter connects the OpenAI Codex Python SDK runtime to Band rooms so it can take part in conversations as a coding collaborator.

Use this adapter when you want an OpenAI-powered coding agent with configurable sandboxing, approval commands, command/file-change telemetry, and reasoning visibility. Use the [Claude SDK adapter](claude_sdk.md) for Claude Code based coding agents, the [Anthropic adapter](anthropic.md) for direct Claude API chat/tool agents, or the [LangGraph adapter](langgraph.md) for custom graph workflows.

## Install

```bash
uv add "band-sdk[codex]"
```

## Prerequisites

The adapter uses the `openai-codex` Python package, which bundles the Codex runtime through `openai-codex-cli-bin`. You need two credentials or auth contexts:

- A Band platform API key for `Agent.create(api_key=...)`.
- Codex authentication for the Codex runtime. Reuse an existing Codex login, run `codex login` if the CLI is available, or set `OPENAI_API_KEY` if that is how your Codex environment is configured.

Credentials for Band can also be loaded from `agent_config.yaml` with `Agent.from_config("my_agent", adapter=adapter)`.

## Quick Start

```python
import asyncio
import os

from band import Agent
from band.adapters.codex import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(
        cwd=os.getcwd(),
        model="gpt-5.5",
    ),
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

Codex has three setup layers:

- `CodexAdapterConfig(...)` configures the Codex runtime: model, working directory, sandbox, approval behavior, prompts, context injection, and streaming/telemetry detail.
- `CodexAdapter(...)` wraps that runtime config for Band and adds adapter-level settings: feature flags, custom tools, history conversion, and advanced client injection.
- `Agent.create(...)` connects the configured adapter to Band. Use it for the Band agent identity, Band API key, platform URLs, session settings, contact-event handling, callbacks, and preprocessing.

Codex authentication is handled by `codex login`, `OPENAI_API_KEY`, or the Codex process environment. `Agent.create(api_key=...)` is only the Band platform key.

Common `Agent.create(...)` parameters:

| Parameter | Use it for |
|-----------|------------|
| `adapter` | The configured `CodexAdapter` instance. |
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

Each Band room maps to one Codex thread. Through Band collaboration tools, Codex can send messages, look up peers, add participants, and create new chats. On startup, the adapter tries to resume the previous Codex thread from thread-mapping metadata. If resume fails and `inject_history_on_resume_failure=True`, the adapter injects recent room history as text context.

The adapter handles Codex approval requests in the room, persists thread metadata through non-task telemetry, and sends optional telemetry such as tool calls, reasoning, diffs, and token usage.

## Configuration Reference

This section covers Codex adapter parameters, not `Agent.create(...)` parameters. `CodexAdapter(...)` has two layers:

- Put runtime settings in `CodexAdapterConfig(...)`.
- Pass adapter-level settings such as `features=` and `additional_tools=` directly to `CodexAdapter(...)`.

```python
from band import AdapterFeatures, Emit
from band.adapters.codex import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(cwd="/repo", sandbox="workspace-write"),
    features=AdapterFeatures(emit={Emit.EXECUTION, Emit.TASK_EVENTS}),
)
```

### Common Runtime Settings

Pass these to `CodexAdapterConfig(...)`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | `str \| None` | `None` | Model to use. When unset, the adapter asks Codex for visible models and uses the first visible model, or the adapter default if discovery fails or returns no usable model. |
| `reasoning_effort` | `"none" \| "minimal" \| "low" \| "medium" \| "high" \| "xhigh" \| None` | `None` | Reasoning effort for models that support it. |
| `reasoning_summary` | `"auto" \| "concise" \| "detailed" \| "none" \| None` | `None` | How Codex summarizes reasoning in responses. |
| `personality` | `"friendly" \| "pragmatic" \| "none"` | `"pragmatic"` | Codex response style. |
| `cwd` | `str \| None` | `None` | Working directory for Codex sessions. |

### Safety, Sandbox, and Approvals

Pass these to `CodexAdapterConfig(...)`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sandbox` | `str \| None` | `None` | Sandbox mode. Common values are `read-only`, `workspace-write`, and `danger-full-access`. |
| `sandbox_policy` | `dict \| None` | `None` | Low-level sandbox policy. When set, room participants cannot override the sandbox with `/sandbox`. |
| `approval_policy` | `str` | `"never"` | Codex CLI approval policy sent to the Codex runtime. |
| `approval_mode` | `"manual" \| "auto_accept" \| "auto_decline"` | `"manual"` | How Band handles Codex approval requests. `"manual"` asks the room to approve or decline. |
| `approval_text_notifications` | `bool` | `True` | Send room messages for approval events. |
| `approval_wait_timeout_s` | `float` | `300.0` | Seconds to wait for a manual approval. |
| `approval_timeout_decision` | `"accept" \| "acceptForSession" \| "decline"` | `"decline"` | Decision when manual approval times out. |
| `session_approval_granularity` | `"binary" \| "full_command"` | `"full_command"` | How `/approve-session` matches future commands. `"full_command"` matches the exact command string; `"binary"` matches the first command token. |
| `max_pending_approvals_per_room` | `int` | `50` | Maximum pending approval requests per room. |
| `max_approval_audit_per_room` | `int` | `100` | Maximum approval audit entries kept per room. |
| `max_session_approved_per_room` | `int` | `100` | Maximum session-level approval patterns kept per room. |

When `approval_mode="manual"`, Codex pauses and the adapter posts a message like:

> Approval requested (execute `npm test`). Approval id: `req-10`.
> Reply `/approve req-10`, `/decline req-10`, or `/approve-session req-10`.
> Use `/approvals` to list pending approvals.

`/approve-session` approves the current request and future similar requests in the same room. The definition of "similar" is controlled by `session_approval_granularity`.

### Prompt and Context

Pass these to `CodexAdapterConfig(...)`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `system_prompt` | `str \| None` | `None` | Replaces the default Band system prompt entirely. |
| `custom_section` | `str` | `""` | Appended to Band's base collaboration prompt. Prefer this over replacing the whole prompt. |
| `include_base_instructions` | `bool` | `True` | Include Band's base collaboration instructions. |
| `inject_history_on_resume_failure` | `bool` | `True` | Inject recent room history as text context when Codex thread resume fails. |
| `max_history_messages` | `int` | `50` | Maximum messages to inject after a resume failure. |
| `fallback_send_agent_text` | `bool` | `True` | Send Codex final text as a room message if Codex did not call the send-message tool. |

### Runtime and Advanced Settings

Pass these to `CodexAdapterConfig(...)`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `codex_command` | `tuple[str, ...] \| None` | `None` | Custom command used to launch Codex instead of the bundled `openai-codex-cli-bin` runtime. |
| `codex_env` | `dict[str, str] \| None` | `None` | Extra environment variables for the Codex process. |
| `experimental_api` | `bool` | `True` | Use experimental Codex API features. |
| `enable_self_config_tools` | `bool` | `False` | Expose tools that let Codex change its own model and reasoning settings. Use only in trusted rooms. |
| `additional_dynamic_tools` | `list[dict]` | `[]` | Extra dynamic tool schemas registered with the Codex client. |
| `client_close_timeout_s` | `float \| None` | `10.0` | Timeout for transport close during cleanup. `None` disables the timeout. |
| `client_name` | `str` | `"band_codex_adapter"` | Client name sent to Codex. |
| `client_title` | `str` | `"Band Codex Adapter"` | Client title sent to Codex. |
| `client_version` | `str` | `"0.1.0"` | Client version sent to Codex. |

Pass these directly to `CodexAdapter(...)`:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `features` | `AdapterFeatures \| None` | `None` | Optional Band feature settings: extra platform-tool capabilities and telemetry emit options. |
| `additional_tools` | `list[CustomToolDef] \| None` | `None` | Custom tools as `(PydanticModel, callable)` tuples. |
| `history_converter` | `CodexHistoryConverter \| None` | auto | Advanced escape hatch for replacing the default history/thread-metadata converter. |
| `client_factory` | callable | `None` | Test/advanced injection point for a custom Codex client. |

## AdapterFeatures: Capabilities and Emit

`AdapterFeatures` is passed to `CodexAdapter(...)`, not to `CodexAdapterConfig(...)`. It has two jobs:

- `capabilities` exposes optional Band tool categories to the model.
- `emit` controls telemetry events the adapter sends back to Band.

If `features` is omitted, Codex defaults to `Emit.TASK_EVENTS` so the adapter can persist thread mapping metadata for reconnects. Optional capabilities are still off by default. If you pass `features=...`, your value is authoritative; include `Emit.TASK_EVENTS` if you want thread resume across reconnects.

| Feature | Supported | What it does |
|---------|-----------|--------------|
| `Capability.CONTACTS` | Yes | Exposes contact-management tools to Codex. Incoming contact request handling is configured separately with `ContactEventConfig` on `Agent.create(...)`. |
| `Capability.MEMORY` | Yes | Exposes memory tools, if memory is enabled for your Band workspace. |
| `Emit.EXECUTION` | Yes | Sends events for command execution, file changes, MCP tools, web search, image viewing, and collaboration-agent tool calls. |
| `Emit.THOUGHTS` | Yes | Sends completed reasoning, plan, and review-mode events as `thought` events. |
| `Emit.TASK_EVENTS` | Yes | Persists Codex thread-mapping metadata for reconnects. Model-authored task updates still come from Band tools. |

Example:

```python
from band import AdapterFeatures, Capability, Emit
from band.adapters.codex import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(model="gpt-5.5"),
    features=AdapterFeatures(
        capabilities={Capability.CONTACTS, Capability.MEMORY},
        emit={Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS},
    ),
)
```

## Telemetry Options

Use `AdapterFeatures.emit` for the broad telemetry categories:

| Emit | Best for | Event output |
|------|----------|--------------|
| `Emit.EXECUTION` | Auditing commands, tools, and file activity. | `tool_call` and `tool_result` pairs. |
| `Emit.THOUGHTS` | Debugging reasoning and planning UX. | Completed `thought` events. |
| `Emit.TASK_EVENTS` | Reconnect continuity. | Non-task thread-mapping metadata. |

Codex also has streaming flags in `CodexAdapterConfig(...)`. These send incremental updates as Codex produces them and are independent of `Emit.THOUGHTS`, which gates completed items.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_reasoning_events` | `bool` | `False` | Stream reasoning chunks as thought events. |
| `stream_plan_events` | `bool` | `False` | Stream plan chunks as thought events and plan updates as execution telemetry. |
| `stream_commentary_events` | `bool` | `False` | Stream commentary chunks as thought events. |

These `CodexAdapterConfig(...)` flags add more telemetry detail:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `enable_task_events` | `bool` | `True` | When `features` is omitted, include `Emit.TASK_EVENTS` by default. Ignored when you pass explicit `features=` to `CodexAdapter(...)`. |
| `emit_diff_events` | `bool` | `False` | Include file diffs in event metadata, capped at 64 KB. |
| `emit_token_usage_events` | `bool` | `False` | Track and emit token usage per session. |
| `structured_errors` | `bool` | `True` | Emit structured error events instead of plain text errors. |

Codex runtime lifecycle events are not mirrored as Band `task` events. Band tasks are reserved for explicit todo/task-list style updates sent through Band tools.

## Chat Commands

Type `/help` in the room to see the command list. Common commands:

| Command | Description |
|---------|-------------|
| `/status` | Show model, room/thread mapping, sandbox, approval state, reasoning settings, and token usage. |
| `/model` or `/models` | Show the current model. |
| `/model list` or `/models list` | List available Codex models. |
| `/model <id>` | Use a model for subsequent turns. |
| `/reasoning <level>` | Set reasoning effort for subsequent turns. |
| `/approvals` | List pending approvals. |
| `/approve <id>` | Approve one pending request. |
| `/approve-session <id>` | Approve this request and future similar requests in the room. |
| `/decline <id>` | Decline one pending request. |
| `/sandbox <mode>` | Set per-room sandbox mode, unless `sandbox_policy` is configured. |
| `/permissions` | Show effective sandbox, approval mode, approval policy, session approvals, and recent approval history. |
| `/threads` | List active room-to-thread mappings. |
| `/thread info` | Show thread ID and token usage for the current room. |
| `/thread archive` | Drop the current room's thread mapping so the next message creates a new thread. |
| `/usage` | Show token usage for the current thread. |

## Custom Tools

Use `additional_tools` on `CodexAdapter(...)` when you want Codex to call functions from your own application.

```python
from pydantic import BaseModel, Field

from band.adapters.codex import CodexAdapter, CodexAdapterConfig


class WeatherInput(BaseModel):
    """Get current weather for a city."""

    city: str = Field(description="City name")


def get_weather(args: WeatherInput) -> str:
    return f"Sunny, 22 C in {args.city}"


adapter = CodexAdapter(
    config=CodexAdapterConfig(model="gpt-5.5"),
    additional_tools=[(WeatherInput, get_weather)],
)
```

The tool name comes from the Pydantic model class, not the callable name. `WeatherInput` becomes `weather`: the SDK strips a trailing `Input` suffix and lowercases the rest. Choose model class names that produce unique tool names, and avoid names that collide with built-in Band tools.

## Examples

See [examples/codex/](../../examples/codex/) for runnable scripts.

| File | Start here when you want to... |
|------|--------------------------------|
| `01_basic_agent.py` | Run a minimal Codex-backed Band agent. |
| `docker-compose.yml` | Run one Codex agent in Docker. |
| `docker-compose.multi.yml` | Run multiple Codex agents together. |
| `docker-compose.plan-review.yml` | Run a planning/review workflow. |
