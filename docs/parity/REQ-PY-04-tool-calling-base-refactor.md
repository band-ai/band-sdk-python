# REQ-PY-04 — Shared tool-calling base refactor

- **Owner SDK:** Python
- **Priority:** P2 (alignment — not a feature gap)
- **Effort:** Medium
- **Status:** Proposed (optional)
- **Parity reference:** TypeScript
  `packages/sdk/src/adapters/tool-calling/` (`ToolCallingAdapter`,
  `ToolCallingModel`)

## Problem

In TypeScript, the agentic tool-calling loop is factored into a single reusable
`ToolCallingAdapter` that the LLM-direct adapters (Anthropic, OpenAI, Gemini,
Vercel) subclass by supplying only a thin `ToolCallingModel.complete()` wrapper
and a `toolFormat`. In Python, each LLM-direct adapter (Anthropic, Gemini, and —
once REQ-PY-01 lands — OpenAI) implements its own loop. This is duplicated logic,
not a missing feature.

This refactor is **optional** and purely about code alignment and maintainability;
it makes future cross-porting between the SDKs cheaper.

## Goal

Extract a shared Python tool-calling loop equivalent to `ToolCallingAdapter`, and
migrate the LLM-direct adapters onto it without changing observable behavior.

## Requirements

### Functional

1. Add a `ToolCallingAdapter` base (e.g. `src/thenvoi/adapters/_tool_calling.py`)
   parameterized by:
   - a `ToolCallingModel` protocol with an async `complete(request)`,
   - a `tool_format` (`"openai" | "anthropic"`),
   - `system_prompt`, `max_tool_rounds`, `enable_execution_reporting`,
     `custom_tools`, `features`.
2. Implement the model→tool-calls→execute→resubmit loop once, using
   `execute_tool_call` and the existing tool-schema providers.
3. Migrate `AnthropicAdapter`, `GeminiAdapter`, and `OpenAIAdapter` (REQ-PY-01) to
   thin `ToolCallingModel` wrappers over this base.

### Acceptance criteria

- No behavioral change: existing adapter and conformance tests pass unchanged
  (or with only mechanical updates).
- The per-adapter loop code is removed in favor of the shared base.
- `ruff` / `pyrefly` clean.

## Affected code (Python)

- `src/thenvoi/adapters/_tool_calling.py` (new base)
- `src/thenvoi/adapters/{anthropic,gemini,openai}.py` (migrate)

## Dependencies

- Best sequenced **after** REQ-PY-01 so OpenAI is migrated at the same time.

## Out of scope

- Agent-framework, coding-agent, and protocol-bridge adapters (they own their
  own loops by design).
