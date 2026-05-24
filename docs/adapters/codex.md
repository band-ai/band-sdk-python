# Codex Adapter

[OpenAI Codex](https://openai.com/codex) is a coding agent runtime that executes commands, edits files, and manages approval workflows. It runs as a local process communicating over stdio or WebSocket.

## Install

```bash
uv add "thenvoi-sdk[codex]"
```

## How It Works

The adapter spawns or connects to a Codex process and maps each Thenvoi room to a Codex thread. Room history is converted to Codex conversation format on session bootstrap, and thread state is persisted in task event metadata so rooms survive reconnections. The adapter handles Codex's approval flow — routing approval requests to the chat room or applying automatic policies — and streams structured events (tool calls, reasoning, diffs) back to Thenvoi based on your emit and streaming configuration.

## Quick Start

```python
from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(model="gpt-5.2"),
)
```

## Configuration Reference

`CodexAdapter` takes a `config` parameter of type `CodexAdapterConfig` and an optional `features` parameter.

### Transport and Model

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `transport` | `"stdio" \| "ws"` | `"stdio"` | How to connect to the Codex process. |
| `model` | `str \| None` | `None` | Model to use. Falls back to `fallback_models` if unset. |
| `reasoning_effort` | `"none" \| "minimal" \| "low" \| "medium" \| "high" \| "xhigh" \| None` | `None` | Model reasoning effort level. |
| `reasoning_summary` | `"auto" \| "concise" \| "detailed" \| "none" \| None` | `None` | How reasoning is summarized in responses. |
| `fallback_models` | `tuple[str, ...]` | `("gpt-5.2", "gpt-5.3-codex")` | Tried when `model` is unset or model list fails. |
| `personality` | `"friendly" \| "pragmatic" \| "none"` | `"pragmatic"` | Codex personality style. |

### Execution and Sandbox

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `cwd` | `str \| None` | `None` | Working directory for the Codex session. |
| `sandbox` | `str \| None` | `None` | Sandbox environment name. |
| `sandbox_policy` | `dict \| None` | `None` | Policy configuration for sandbox. |
| `codex_command` | `tuple[str, ...] \| None` | `None` | Custom command to launch the Codex process. |
| `codex_env` | `dict[str, str] \| None` | `None` | Extra environment variables for the Codex process. |
| `codex_ws_url` | `str` | `"ws://127.0.0.1:8765"` | WebSocket URL when `transport="websocket"`. |
| `turn_timeout_s` | `float` | `180.0` | Max seconds per Codex turn before timeout. |

### Approval Handling

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `approval_policy` | `str` | `"never"` | Codex CLI approval policy. |
| `approval_mode` | `"manual" \| "auto_accept" \| "auto_decline"` | `"manual"` | How the adapter handles approval requests. `"manual"` routes to the chat room via `/approve` and `/decline`. |
| `approval_text_notifications` | `bool` | `True` | Send room messages for approval events. |
| `approval_wait_timeout_s` | `float` | `300.0` | Seconds to wait for a manual approval decision. |
| `approval_timeout_decision` | `"accept" \| "decline"` | `"decline"` | Default decision when approval times out. |
| `session_approval_granularity` | `"binary" \| "full_command"` | `"full_command"` | How `/approve-session` patterns match. `"full_command"` matches the exact command string. `"binary"` matches only the first token (e.g. approving `npm test` auto-approves all future `npm` commands). |
| `max_pending_approvals_per_room` | `int` | `50` | Upper bound on pending approval requests per room. |
| `max_approval_audit_per_room` | `int` | `100` | Upper bound on approval audit entries per room. |
| `max_session_approved_per_room` | `int` | `100` | Upper bound on session-level auto-approved patterns. Evicted LRU. |

### Prompts and Instructions

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `system_prompt` | `str \| None` | `None` | Replace the SDK's default system prompt entirely. |
| `custom_section` | `str` | `""` | Appended to the SDK's base prompt. Prefer this over `system_prompt`. |
| `include_base_instructions` | `bool` | `True` | Include the SDK's base collaboration instructions. |

### Other

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `fallback_send_agent_text` | `bool` | `True` | Send the agent's final text as a room message if no `send_message` tool call was made. |
| `experimental_api` | `bool` | `True` | Use experimental Codex API features. |
| `enable_self_config_tools` | `bool` | `False` | Expose tools that let the agent change its own config. |
| `additional_dynamic_tools` | `list[dict]` | `[]` | Extra tool schemas to register with the Codex client. |
| `inject_history_on_resume_failure` | `bool` | `True` | Inject room history as context when thread resume fails. |
| `max_history_messages` | `int` | `50` | Max messages to inject on resume. |
| `client_close_timeout_s` | `float \| None` | `10.0` | Timeout for transport close during cleanup. `None` disables. |
| `client_name` | `str` | `"thenvoi_codex_adapter"` | Client name sent to Codex. |
| `client_title` | `str` | `"Thenvoi Codex Adapter"` | Client title sent to Codex. |
| `client_version` | `str` | `"0.1.0"` | Client version sent to Codex. |

## Capabilities and Emit

| Feature | Supported |
|---------|-----------|
| `Capability.CONTACTS` | Yes |
| `Capability.MEMORY` | Yes |
| `Emit.EXECUTION` | Yes |
| `Emit.THOUGHTS` | Yes |
| `Emit.TASK_EVENTS` | Yes |

Codex supports the full emit surface:

```python
from thenvoi import AdapterFeatures, Capability, Emit
from thenvoi.adapters.codex import CodexAdapter, CodexAdapterConfig

adapter = CodexAdapter(
    config=CodexAdapterConfig(model="gpt-5.2"),
    features=AdapterFeatures(
        capabilities={Capability.MEMORY},
        emit={Emit.EXECUTION, Emit.THOUGHTS, Emit.TASK_EVENTS},
    ),
)
```

### What Each Emit Type Sends

| Emit | Events | Source Items |
|------|--------|--------------|
| `EXECUTION` | `tool_call` + `tool_result` pairs | `commandExecution`, `fileChange`, `mcpToolCall`, `webSearch`, `imageView`, `collabAgentToolCall` |
| `THOUGHTS` | `thought` events | `reasoning`, `plan`, `contextCompaction`, `enteredReviewMode`, `exitedReviewMode` |
| `TASK_EVENTS` | Task lifecycle events | Turn start/complete, status transitions, token usage, diff summaries, error reports |

### Streaming Flags

In addition to emit, Codex has real-time streaming flags that send deltas as they arrive from the Codex runtime. These are independent of `Emit.THOUGHTS` — emit gates completed items, streaming sends incremental chunks.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `stream_reasoning_events` | `bool` | `False` | Stream reasoning chunks as thought events. |
| `stream_plan_events` | `bool` | `False` | Stream plan chunks as thought events. |
| `stream_commentary_events` | `bool` | `False` | Stream commentary chunks as thought events. |

Both mechanisms produce `thought` message-type events but at different granularities: emit sends one event per completed item; streaming sends many small events as the runtime produces them.

### Telemetry Config Flags

These `CodexAdapterConfig` booleans provide additional telemetry control:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `emit_turn_task_markers` | `bool` | `False` | Emit simple "Codex turn" task markers on turn completion. |
| `emit_turn_lifecycle_events` | `bool` | `False` | Emit enriched turn lifecycle events (started + completed with metadata). |
| `emit_diff_events` | `bool` | `False` | Include file diffs in event metadata (capped at 64 KB). |
| `emit_token_usage_events` | `bool` | `False` | Track and emit token usage per session. |
| `structured_errors` | `bool` | `True` | Emit structured error events vs plain text. |

> Enabling both `emit_turn_task_markers` and `emit_turn_lifecycle_events` produces **two** task events per completed turn. Pick one — prefer lifecycle for richer metadata.

### Deprecated Booleans

These config booleans are deprecated. Use `AdapterFeatures(emit={...})` instead. Do not mix deprecated booleans with `features=`.

| Deprecated | Replacement |
|-----------|-------------|
| `enable_execution_reporting=True` | `Emit.EXECUTION` |
| `emit_thought_events=True` | `Emit.THOUGHTS` |
| `enable_task_events=True` | `Emit.TASK_EVENTS` |

## Examples

See [examples/codex/](../../examples/codex/) for runnable scripts.

| File | Description |
|------|-------------|
| `01_basic_agent.py` | Minimal Codex agent |
| `docker-compose.yml` | Single-agent Docker setup |
| `docker-compose.multi.yml` | Multi-agent Docker setup |
| `docker-compose.plan-review.yml` | Plan-review workflow |
