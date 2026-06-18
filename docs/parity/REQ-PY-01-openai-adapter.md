# REQ-PY-01 — OpenAI direct adapter

- **Owner SDK:** Python
- **Priority:** P0
- **Effort:** Small
- **Status:** Proposed
- **Parity reference:** TypeScript `packages/sdk/src/adapters/openai/`
  (`OpenAIAdapter` extending `ToolCallingAdapter`)

## Problem

The TypeScript SDK ships a first-class `OpenAIAdapter` (LLM-direct, default model
`gpt-5.2`, wraps the `openai` client through the shared tool-calling loop). The
Python SDK has **no direct OpenAI adapter** — OpenAI is only reachable indirectly
(e.g. via `PydanticAIAdapter` model strings or LangGraph). This is the cheapest
parity gap to close because the tool-calling loop already exists in the Python
Anthropic and Gemini adapters.

## Goal

Add an `OpenAIAdapter` to the Python SDK that mirrors the existing LLM-direct
adapters in structure and behavior.

## Requirements

### Functional

1. Add `src/thenvoi/adapters/openai.py` with `OpenAIAdapter(SimpleAdapter[...])`
   following the same shape as `AnthropicAdapter`:
   - constructor: `model` (default a current GPT model), `api_key`,
     `system_prompt` / `prompt`, `custom_section`, `max_tokens` (or
     `max_output_tokens`), `additional_tools`, `features`, `history_converter`,
     `include_base_instructions`.
2. Add `src/thenvoi/converters/openai.py` with `OpenAIHistoryConverter`
   implementing `HistoryConverter`, using `get_openai_tool_schemas()` for the
   tool surface. Reuse `_tool_parsing.parse_tool_call` / `parse_tool_result`.
3. Wire `SUPPORTED_EMIT` / `SUPPORTED_CAPABILITIES` and `AdapterFeatures` support
   consistently with the other adapters.
4. Add the `openai` optional-dependency extra to `pyproject.toml`.
5. Add an `examples/openai/` script with PEP 723 metadata (per CLAUDE.md example
   rules) and `load_agent_config(...)`.

### Acceptance criteria

- `OpenAIAdapter` and `OpenAIHistoryConverter` are exported from the package
  `__init__` surfaces consistent with sibling adapters.
- Registered with the conformance infrastructure
  (`tests/framework_configs/{adapters,converters,output_adapters}.py`) and passes
  `tests/framework_conformance/`.
- Unit tests in `tests/adapters/test_openai_adapter.py` and
  `tests/converters/test_openai.py` cover LLM invocation, tool execution, error
  handling, and custom tools.
- `uv run ruff check . && uv run ruff format . && uv run pyrefly check` clean.

## Affected code (Python)

- `src/thenvoi/adapters/openai.py` (new)
- `src/thenvoi/converters/openai.py` (new)
- `pyproject.toml` (new `openai` extra)
- conformance config registries + tests
- `examples/openai/` (new)

## Dependencies

None. Follow the "Adding a New Framework Integration" TDD workflow in CLAUDE.md.

## Out of scope

- Refactoring the shared loop (tracked separately as REQ-PY-04).
